# Add-on config section: COM port and custom voices.
# Kept outside the synth section so the port can be changed while another
# synthesizer is active.

import config

SECTION = "dectalkExpress"

config.conf.spec[SECTION] = {
	"port": "string(default='')",
	"customVoices": "string(default='{}')",  # JSON, see _voicestore
}

#: Used until a port has been chosen.
FALLBACK_PORT = "COM1"


def getPort():
	try:
		port = config.conf[SECTION]["port"].strip()
	except Exception:
		port = ""
	return port or FALLBACK_PORT


def isPortConfigured():
	try:
		return bool(config.conf[SECTION]["port"].strip())
	except Exception:
		return False


def setPort(port):
	config.conf[SECTION]["port"] = port


def listPorts():
	"""Available COM ports as (port, description), sorted by number."""
	ports = []
	try:
		import hwPortUtils
		for entry in hwPortUtils.listComPorts(onlyAvailable=True):
			port = entry.get("port")
			if port:
				ports.append((port, entry.get("friendlyName") or entry.get("bluetoothName") or ""))
	except Exception:
		pass

	def sortKey(item):
		name = item[0].upper()
		num = name[3:] if name.startswith("COM") else ""
		return (0, int(num), name) if num.isdigit() else (1, 0, name)

	seen = set()
	unique = []
	for port, desc in sorted(ports, key=sortKey):
		if port.upper() not in seen:
			seen.add(port.upper())
			unique.append((port, desc))
	return unique
