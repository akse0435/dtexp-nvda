# DECtalk Express firmware upgrade: a port of DEC's mfg_load.c.
#
# Steps (as in mfg_load):
#   1. Open the port at 9600 8N1, no flow control.
#   2. Enter the unit's console: ENQ -> DLE, then DLE "&@@" and DLE "*@@".
#      After a Ctrl+C (which the driver sends to stop speech) the unit only
#      answers XON and Ctrl+A to the first try, so step 2 is run again,
#      exactly as if mfg_load were started a second time.
#   3. Optionally switch the console and the port to 57600 baud.
#   4. "FD 0 E8000" fills memory with 0; "LOAD 0" sets the load base.
#   5. Send mon.hex and fastload.hxo line by line, each answered by ">".
#   6. "g 90000" starts the fast loader; send out.flr; "X" leaves it.
#   7. Back to 9600 baud; "RB" asks to program the flash, "Y" confirms.
#   8. Wait until the unit is quiet. The user then cycles the power.
# The files only go to RAM; nothing is written to flash before step 7, so
# cancelling and cycling the power leaves the old firmware in place.
# Bytes, order and waits are exactly mfg_load's.

import logging
import os
import time

import serial

try:
	from logHandler import log
except ImportError:  # outside NVDA (tests)
	log = logging.getLogger("dectalkexpress")

FILES = ("mon.hex", "fastload.hxo", "out.flr")

DLE = 0x10
ENQ = b"\x05"
CONSOLE_STRINGS = (b"\x10&@@", b"\x10*@@")


class FlashError(Exception):
	def __init__(self, message, log=""):
		super(FlashError, self).__init__(message)
		self.log = log


class FlashCancelled(Exception):
	pass


def findFiles(folder):
	"""Paths of the three update files in `folder` (any letter case), or
	raise FlashError naming the missing ones."""
	try:
		names = {name.lower(): name for name in os.listdir(folder)}
	except OSError as e:
		raise FlashError("cannot read %s: %s" % (folder, e))
	missing = [f for f in FILES if f not in names]
	if missing:
		raise FlashError("missing in %s: %s" % (folder, ", ".join(missing)))
	return [os.path.join(folder, names[f]) for f in FILES]


class Flasher(object):
	"""Run with run(); call cancel() from another thread to stop before the
	flash is written. `progress(fraction or None, text)` reports status; while
	loading, `fraction` is for the current file."""

	def __init__(self, portName, files, fast=True, progress=None):
		self.portName = portName
		self.files = files
		self.fast = fast
		self._progress = progress or (lambda fraction, text: None)
		self._cancelled = False
		self.canCancel = True
		self._port = None
		self._pushback = bytearray()
		self._log = bytearray()  # last characters from the unit, for errors
		self._verbose = True  # log the dialogue (not the file lines)

	def cancel(self):
		if self.canCancel:
			self._cancelled = True

	# -- serial helpers (mfg_load's send_deport and friends) --

	def _read(self):
		if self._pushback:
			data = bytes(self._pushback)
			del self._pushback[:]
			return data
		data = self._port.read(self._port.in_waiting or 1)
		if data and self._verbose:
			log.debug("DECtalk Express flash RX: %r" % (data,))
		return data

	def _write(self, data):
		if self._verbose:
			log.debug("DECtalk Express flash TX: %r" % (data,))
		self._port.write(data)

	def _remember(self, data):
		self._log += data
		del self._log[:-1024]

	def _send(self, data, wait):
		"""Send `data`, then read until ">" or `wait` quarter seconds pass.
		Returns "?" if a "?" was seen, else the last character, else None."""
		if data:
			self._write(data)
		end = time.time() + wait * 0.25
		sawQuest = False
		last = None
		while time.time() < end:
			data = bytearray(self._read())
			for i, b in enumerate(data):
				self._remember(bytes((b,)))
				last = b
				if b == 0x3F:  # ?
					sawQuest = True
				if b == 0x3E:  # >
					self._pushback += data[i + 1:]
					return "?" if sawQuest else ">"
		if sawQuest:
			return "?"
		return chr(last) if last is not None else None

	def _drain(self, seconds):
		"""Read and log everything for `seconds`."""
		end = time.time() + seconds
		while time.time() < end:
			self._remember(self._read())

	def _setBaud(self, baud):
		log.debug("DECtalk Express flash: %d baud" % baud)
		self._port.baudrate = baud

	def _check(self):
		if self._cancelled:
			raise FlashCancelled()

	def _logText(self):
		return "".join(chr(b) if 32 <= b < 127 or b in (10, 13) else "." for b in self._log[-300:])

	def _fail(self, message):
		raise FlashError(message, self._logText())

	# -- the load --

	def _open(self):
		"""Open the port like mfg_load's open_port."""
		try:
			self._port = serial.Serial(
				port=self.portName, baudrate=9600, bytesize=8, parity="N", stopbits=1,
				xonxoff=False, rtscts=False, dsrdtr=False, timeout=0.01, write_timeout=10,
			)
		except Exception as e:
			raise FlashError("cannot open %s: %s" % (self.portName, e))
		self._port.reset_input_buffer()
		self._port.reset_output_buffer()
		del self._pushback[:]
		log.debug("DECtalk Express flash: %s opened (%s)" % (self.portName, self._port.get_settings()))

	def _close(self):
		try:
			self._port.close()
		except Exception:
			pass

	def run(self):
		self._open()
		try:
			if not self._enterConsole():
				# Second mfg_load run; see the top of this file.
				log.debug("DECtalk Express flash: no console yet, second try")
				self._close()
				self._check()
				self._open()
				if not self._enterConsole(again=True):
					self._fail("couldn't start the DECtalk Express console")
			self._check()
			self._goFast()
			self._progress(0.0, "Clearing memory")
			if self._send(b"FD 0 E8000\r0\r", 4) != ">":
				self._fail("the unit didn't clear its memory")
			if self._send(b"LOAD 0\r", 1) != ">":
				self._fail("the unit didn't accept the load address")
			self._loadFile(self.files[0], fatal=False)
			self._loadFile(self.files[1], fatal=False)
			self._send(b"g 90000\r", 1)
			self._loadFile(self.files[2], fatal=True)
			self._send(b"X", 1)
			self._check()
			if self._port.baudrate > 9600:
				self._send(b"c b9600\r", 1)
				self._setBaud(9600)
				self._send(b"\r", 1)
			self._program()
		finally:
			self._close()

	def _enterConsole(self, again=False):
		"""mfg_load's console entry; True if the console answered."""
		self._progress(None, "Contacting the DECtalk Express")
		self._send(b"\r", 1)
		if self._send(b"\r", 1) == ">":
			log.debug("DECtalk Express flash: already in the console")
			return True
		self._write(ENQ)
		end = time.time() + 2
		found = False
		while time.time() < end and not found:
			data = self._read()
			self._remember(data)
			found = DLE in bytearray(data)
		if not found:
			if again:  # the unit was found the first time
				return False
			raise FlashError("no DECtalk Express found on %s" % self.portName)
		self._drain(2)
		self._send(CONSOLE_STRINGS[0], 1)
		self._send(CONSOLE_STRINGS[1], 2)
		self._drain(2)
		return self._send(b"\r", 1) == ">"

	def _goFast(self):
		if not self.fast:
			return
		self._send(b"c b57600\r", 1)
		self._setBaud(57600)
		self._drain(2)
		if self._send(b"\r", 1) != ">":
			self._setBaud(9600)
			self._send(b"\r", 1)

	def _loadFile(self, path, fatal):
		name = os.path.basename(path)
		log.debug("DECtalk Express flash: loading %s" % name)
		with open(path, "rb") as f:
			data = f.read()
		lines = data.split(b"\n")
		# Like mfg_load, only complete lines (ending in a newline) are sent.
		total = float(max(len(data) - len(lines[-1]), 1))
		done = 0
		text = "Loading %s" % name
		self._progress(0.0, text)
		for i, line in enumerate(lines[:-1]):
			self._check()
			done += len(line) + 1
			if line.endswith(b"\r"):
				line = line[:-1]
			line += b"\r"
			self._verbose = False
			k = self._send(line, 1)
			self._verbose = True
			if k != ">":
				log.debug("DECtalk Express flash: line %d of %s answered %r: %r" % (i + 1, name, k, line))
				if not fatal:
					# One retry, as in mfg_load.
					if self._send(b"\r", 1) != ">":
						self._fail("lost the connection while sending %s" % name)
					k = self._send(line, 1)
				if k != ">":
					self._fail("the unit rejected a line of %s" % name)
			if i % 64 == 0:
				self._progress(done / total, text)
		self._progress(1.0, text)

	def _program(self):
		self._progress(1.0, "Starting to write the flash memory")
		k = self._send(b"RB\r", 4)
		if k != ":":
			# The ">" may have been in the message; look again.
			k = self._send(b"", 4)
		if k != ":":
			self._fail("the unit didn't offer to program the flash")
		self.canCancel = False
		self._progress(None, "Writing the flash memory. Do not turn off the DECtalk Express!")
		self._send(b"Y", 1)
		last = time.time()
		received = 0
		while time.time() - last < 5:
			data = self._read()
			if data:
				self._remember(data)
				received += len(data)
				last = time.time()
				if received % 256 < len(data):
					self._progress(None, "Writing the flash memory. Do not turn off the DECtalk Express!")
		self._send(b"\r", 1)
