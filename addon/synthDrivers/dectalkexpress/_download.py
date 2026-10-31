# DECtalk Express firmware downloads.
#
# LIST_URL has a block of `key = "value"` lines per firmware: name, file
# (relative to the list), size and SHA256. Blocks are separated by blank
# lines or "---". Each file is a ZIP with mon.hex, fastload.hxo and out.flr;
# it is downloaded to a temporary folder, checked against its SHA256 and
# unpacked there. cleanup() deletes the folder.

import ctypes
import hashlib
import logging
import os
import re
import shutil
import ssl
import tempfile
import zipfile
from urllib.error import URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from ._flash import FILES, FlashCancelled, FlashError, findFiles

try:
	from logHandler import log
except ImportError:  # outside NVDA (tests)
	log = logging.getLogger("dectalkexpress")

LIST_URL = "https://download.dectalk.nu/dtexp/files.txt"
TIMEOUT = 15  # seconds
CHUNK_SIZE = 8192
USER_AGENT = "NVDA DECtalk Express add-on"

_LINE = re.compile(r"^\s*([A-Za-z0-9_]+)\s*=\s*(.*?)\s*$")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


class Firmware(object):
	"""One firmware on the server."""

	def __init__(self, name, url, size, sha256):
		self.name = name
		self.url = url
		self.size = size  # bytes, or None
		self.sha256 = sha256.lower()


def _isCertError(e):
	reason = getattr(e, "reason", None)
	return isinstance(e, URLError) and isinstance(reason, ssl.SSLError) and "CERTIFICATE_VERIFY_FAILED" in str(reason)


class _CERT_USAGE_MATCH(ctypes.Structure):
	_fields_ = (
		("dwType", ctypes.c_ulong),
		("cUsageIdentifier", ctypes.c_ulong),
		("rgpszUsageIdentifier", ctypes.c_void_p),
	)


class _CERT_CHAIN_PARA(ctypes.Structure):
	_fields_ = (
		("cbSize", ctypes.c_ulong),
		("RequestedUsage", _CERT_USAGE_MATCH),
	)


def _updateRootCertificates(request):
	"""Has Windows build the certificate chain of the server, which makes it
	fetch a missing root certificate. NVDA's update check does the same, but
	only for its own server. Python only uses roots Windows already has."""
	unverified = ssl.create_default_context()
	unverified.check_hostname = False
	unverified.verify_mode = ssl.CERT_NONE
	response = urlopen(request, timeout=TIMEOUT, context=unverified)
	try:
		cert = response.fp.raw._sock.getpeercert(True)
	finally:
		response.close()
	# Our own handle, so the argument types don't change NVDA's.
	crypt32 = ctypes.WinDLL("crypt32")
	crypt32.CertCreateCertificateContext.restype = ctypes.c_void_p
	crypt32.CertCreateCertificateContext.argtypes = (ctypes.c_ulong, ctypes.c_char_p, ctypes.c_ulong)
	crypt32.CertGetCertificateChain.argtypes = (
		ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
		ctypes.POINTER(_CERT_CHAIN_PARA), ctypes.c_ulong, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
	)
	crypt32.CertFreeCertificateChain.argtypes = (ctypes.c_void_p,)
	crypt32.CertFreeCertificateContext.argtypes = (ctypes.c_void_p,)
	certContext = crypt32.CertCreateCertificateContext(1, cert, len(cert))  # X509_ASN_ENCODING
	if not certContext:
		raise ctypes.WinError()
	try:
		chain = ctypes.c_void_p()
		para = _CERT_CHAIN_PARA(cbSize=ctypes.sizeof(_CERT_CHAIN_PARA))
		if crypt32.CertGetCertificateChain(None, certContext, None, None, ctypes.byref(para), 0, None, ctypes.byref(chain)):
			crypt32.CertFreeCertificateChain(chain)
	finally:
		crypt32.CertFreeCertificateContext(certContext)


def _open(url):
	"""urlopen(url). On a certificate error, has Windows update its root
	certificates for the server and tries again."""
	request = Request(url, headers={"User-Agent": USER_AGENT})
	try:
		# A new context each time, so newly added roots are loaded.
		return urlopen(request, timeout=TIMEOUT, context=ssl.create_default_context())
	except URLError as e:
		if not _isCertError(e):
			raise
		log.warning("DECtalk Express: certificate error for %s (%s), updating root certificates" % (url, e.reason))
	try:
		_updateRootCertificates(request)
	except Exception:
		log.warning("DECtalk Express: could not update root certificates", exc_info=True)
	return urlopen(request, timeout=TIMEOUT, context=ssl.create_default_context())


def _unquote(value):
	if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
		return value[1:-1]
	return value


def _firmware(block, baseUrl):
	"""The Firmware described by `block` (lower-case keys), or None."""
	name = block.get("name")
	fileName = block.get("file")
	sha256 = block.get("sha256", "")
	if not (name and fileName and _SHA256.match(sha256)):
		log.warning("DECtalk Express: firmware list entry skipped: %r" % (block,))
		return None
	try:
		size = int(block.get("size", ""))
	except ValueError:
		size = None
	return Firmware(name, urljoin(baseUrl, fileName), size, sha256)


def parseList(text, baseUrl=LIST_URL):
	"""The firmwares in `text`, in the list's order."""
	firmwares = []
	block = {}
	for line in text.splitlines() + [""]:
		match = _LINE.match(line)
		key = match.group(1).lower() if match else None
		if key is not None and key not in block:
			block[key] = _unquote(match.group(2))
			continue
		# A blank line, "---" or a repeated key ends the block.
		if block:
			firmware = _firmware(block, baseUrl)
			if firmware is not None:
				firmwares.append(firmware)
		block = {key: _unquote(match.group(2))} if key is not None else {}
	return firmwares


def fetchList():
	"""The firmwares that can be downloaded; raises on network errors."""
	response = _open(LIST_URL)
	try:
		text = response.read().decode("utf-8", "replace")
	finally:
		response.close()
	firmwares = parseList(text, LIST_URL)
	log.info("DECtalk Express: %d firmware downloads available" % len(firmwares))
	return firmwares


class Download(object):
	"""Run with run(); call cancel() from another thread to stop.
	`progress(fraction or None, text)` reports status. The files stay in
	`folder` until cleanup()."""

	canCancel = True

	def __init__(self, firmware, progress=None):
		self.firmware = firmware
		self.folder = None
		self.files = None
		self._progress = progress or (lambda fraction, text: None)
		self._cancelled = False

	def cancel(self):
		self._cancelled = True

	def _check(self):
		if self._cancelled:
			raise FlashCancelled()

	def run(self):
		"""Paths of the three update files, as _flash.findFiles returns them."""
		self.folder = tempfile.mkdtemp(prefix="dectalkExpress-")
		try:
			zipPath = os.path.join(self.folder, "firmware.zip")
			self._fetch(zipPath)
			self._check()
			self._unpack(zipPath)
			self.files = findFiles(self.folder)
		except Exception:
			self.cleanup()
			raise
		return self.files

	def _fetch(self, path):
		name = self.firmware.name
		text = "Downloading %s" % name
		self._progress(0.0, text)
		digest = hashlib.sha256()
		done = 0
		percent = 0
		try:
			response = _open(self.firmware.url)
			try:
				total = self.firmware.size or int(response.headers.get("Content-Length") or 0)
				with open(path, "wb") as f:
					while True:
						self._check()
						chunk = response.read(CHUNK_SIZE)
						if not chunk:
							break
						f.write(chunk)
						digest.update(chunk)
						done += len(chunk)
						if not total:
							self._progress(None, text)
						elif int(done * 100 / total) != percent:
							percent = int(done * 100 / total)
							self._progress(min(done / float(total), 1.0), text)
			finally:
				response.close()
		except OSError as e:  # also URLError and timeouts
			raise FlashError("could not download %s: %s" % (name, e))
		if digest.hexdigest() != self.firmware.sha256:
			log.error("DECtalk Express: SHA256 of %s is %s, expected %s" % (
				self.firmware.url, digest.hexdigest(), self.firmware.sha256))
			raise FlashError("the download of %s is damaged (wrong SHA256 checksum)" % name)
		log.info("DECtalk Express: downloaded %s (%d bytes), SHA256 matches" % (self.firmware.url, done))
		self._progress(1.0, text)

	def _unpack(self, zipPath):
		self._progress(None, "Unpacking %s" % self.firmware.name)
		try:
			with zipfile.ZipFile(zipPath) as z:
				# Only the update files, by name, wherever they are in the ZIP.
				members = {}
				for info in z.infolist():
					name = os.path.basename(info.filename.replace("\\", "/").rstrip("/")).lower()
					if name in FILES:
						members[name] = info
				missing = [name for name in FILES if name not in members]
				if missing:
					raise FlashError("missing in %s: %s" % (self.firmware.name, ", ".join(missing)))
				for name in FILES:
					with z.open(members[name]) as src, open(os.path.join(self.folder, name), "wb") as dst:
						shutil.copyfileobj(src, dst)
		except (zipfile.BadZipfile, OSError) as e:
			raise FlashError("cannot unpack %s: %s" % (self.firmware.name, e))

	def cleanup(self):
		"""Delete the ZIP file and the unpacked files."""
		folder, self.folder = self.folder, None
		self.files = None
		if folder:
			shutil.rmtree(folder, ignore_errors=True)
			log.debug("DECtalk Express: deleted %s" % folder)
