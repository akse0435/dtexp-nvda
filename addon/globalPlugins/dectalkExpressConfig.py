# DECtalk Express Configuration Utility (NVDA menu -> Tools), with a General
# tab (firmware and battery status, firmware upgrade) and a Voice Manager tab,
# plus the DECtalk Express settings panel (COM port). The voice manager is
# modelled on the DECtalk Software add-on's, with its own names, config
# section and synth id.

import threading

import globalPluginHandler
import gui
import synthDriverHandler
import wx
from gui import guiHelper
from logHandler import log

try:
	from gui.settingsDialogs import NVDASettingsDialog, SettingsPanel
except ImportError:
	NVDASettingsDialog = SettingsPanel = None

try:
	from synthDrivers.dectalkexpress import _conf, _flash, _params, _serialio, _voicestore
except Exception:
	_conf = _flash = _params = _serialio = _voicestore = None
	log.exception("DECtalk Express configuration utility: driver package unavailable")

SYNTH_NAME = "dectalkexpress"
TITLE = "DECtalk Express Configuration Utility"
PREVIEW_TEXT = "DECtalk voice preview. The quick brown fox jumps over the lazy dog."
#: Synthesizers NVDA is switched to while the firmware is upgraded.
FALLBACK_SYNTHS = ("espeak", "oneCore", "sapi5")

# Status items: (key, label, speak command, status command).
STATUS_ITEMS = (
	("version", "Firmware version", b"[:version speak].", b"[:version status]."),
	("power", "Power status", b"[:power speak].", b"[:power status]."),
)


def _statusText(key, answer):
	"""'[:version V4 2 - CD ...]' -> 'V4 2 - CD ...'."""
	if not answer:
		return "no answer"
	text = " ".join(answer).strip()
	prefix = "[:%s" % key
	if text.lower().startswith(prefix) and text.endswith("]"):
		text = text[len(prefix):-1].strip() or text
	return text


def _activeExpress():
	"""NVDA's synthesizer if it is the DECtalk Express driver, else None."""
	synth = synthDriverHandler.getSynth()
	if synth is None:
		return None
	if getattr(synth, "name", None) == SYNTH_NAME or type(synth).__module__.startswith("synthDrivers." + SYNTH_NAME):
		return synth
	return None


def _previewCommands(base, params):
	"""Commands selecting `base` with `params`, for a unit not driven by NVDA."""
	defaults = _params.VOICE_DEFAULTS[base]
	dv = " ".join(
		"%s %d" % (code, params[code]) for code in _params.PARAM_CODES
		if params[code] != defaults[code]
	)
	cmd = "[:n%s][:sa c]" % _params.VOICES[base][0]
	return cmd + ("[:dv %s]" % dv if dv else "")


def _speak(text):
	try:
		import ui
		ui.message(text)
	except Exception:
		pass


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	def __init__(self):
		super().__init__()
		self._menuItem = None
		if _voicestore is None:
			return
		if NVDASettingsDialog is not None:
			NVDASettingsDialog.categoryClasses.append(DECtalkExpressSettingsPanel)
		self._menuItem = gui.mainFrame.sysTrayIcon.toolsMenu.Append(
			wx.ID_ANY,
			"DECtalk E&xpress Configuration Utility...",
			"DECtalk Express firmware, battery and custom voices",
		)
		gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self._onOpen, self._menuItem)

	def terminate(self):
		if _voicestore is None:
			return
		if NVDASettingsDialog is not None:
			try:
				NVDASettingsDialog.categoryClasses.remove(DECtalkExpressSettingsPanel)
			except ValueError:
				pass
		try:
			gui.mainFrame.sysTrayIcon.toolsMenu.Remove(self._menuItem)
		except Exception:
			pass

	def _onOpen(self, evt):
		ConfigDialog.open()


class ConfigDialog(wx.Dialog):
	"""The utility's window: General and Voice Manager tabs."""

	_instance = None

	@classmethod
	def open(cls):
		if cls._instance is not None:
			try:
				cls._instance.Raise()
				return
			except RuntimeError:
				cls._instance = None
		gui.mainFrame.prePopup()
		try:
			cls._instance = cls(gui.mainFrame)
			cls._instance.Show()
		finally:
			gui.mainFrame.postPopup()

	def __init__(self, parent):
		super().__init__(parent, title=TITLE)
		main = wx.BoxSizer(wx.VERTICAL)
		self.notebook = wx.Notebook(self)
		self.general = GeneralPanel(self.notebook)
		self.voices = VoiceManagerPanel(self.notebook)
		self.notebook.AddPage(self.general, "General")
		self.notebook.AddPage(self.voices, "Voice Manager")
		main.Add(self.notebook, proportion=1, flag=wx.EXPAND | wx.ALL, border=8)
		main.Add(self.CreateButtonSizer(wx.CLOSE), flag=wx.ALL | wx.ALIGN_RIGHT, border=8)
		self.Bind(wx.EVT_BUTTON, self.onClose, id=wx.ID_CLOSE)
		self.Bind(wx.EVT_CLOSE, self.onClose)
		self.SetEscapeId(wx.ID_CLOSE)
		self.SetSizerAndFit(main)
		self.CentreOnScreen()
		self.general.readStatus()

	def onClose(self, evt):
		ConfigDialog._instance = None
		self.general.link.close()
		self.Destroy()


class _Link(object):
	"""The way to the unit: the driver's connection when DECtalk Express is
	NVDA's synthesizer, else a connection of our own, opened when first
	needed and closed with the utility."""

	def __init__(self):
		self._own = None
		self._lock = threading.Lock()

	def get(self, synth):
		"""(connection, port) to use. Opens our own connection if needed;
		call from a worker thread."""
		with self._lock:
			if synth is not None and synth._conn is not None:
				self._closeOwn()
				return synth._conn, synth.portName
			if self._own is None:
				# The same port the driver uses (COM1 until one is chosen).
				self._own = _serialio.Connection(_conf.getPort())
				log.info("DECtalk Express configuration utility: opened %s" % self._own.portName)
			return self._own, self._own.portName

	def close(self):
		with self._lock:
			self._closeOwn()

	def _closeOwn(self):
		if self._own is not None:
			own, self._own = self._own, None
			try:
				own.close(stop=False)  # let the unit finish speaking
			except Exception:
				log.error("DECtalk Express: closing the port failed", exc_info=True)
			log.info("DECtalk Express configuration utility: closed %s" % own.portName)


class GeneralPanel(wx.Panel):
	"""Firmware and power status, and the firmware upgrade."""

	def __init__(self, parent):
		super().__init__(parent)
		self._values = {}
		self._busy = False
		self.link = _Link()
		helper = guiHelper.BoxSizerHelper(self, orientation=wx.VERTICAL)
		self.statusText = helper.addLabeledControl(
			"&Status:", wx.TextCtrl, size=(420, 110),
			style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2,
		)
		buttons = guiHelper.ButtonHelper(wx.HORIZONTAL)
		self.versionButton = buttons.addButton(self, label="Check &firmware version")
		self.powerButton = buttons.addButton(self, label="Check &battery status")
		self.upgradeButton = buttons.addButton(self, label="&Upgrade firmware...")
		helper.addItem(buttons)
		self.versionButton.Bind(wx.EVT_BUTTON, lambda e: self.check("version"))
		self.powerButton.Bind(wx.EVT_BUTTON, lambda e: self.check("power"))
		self.upgradeButton.Bind(wx.EVT_BUTTON, self.onUpgrade)
		border = wx.BoxSizer(wx.VERTICAL)
		border.Add(helper.sizer, flag=wx.ALL | wx.EXPAND, border=8)
		self.SetSizerAndFit(border)

	def _show(self, port=None, message=None):
		lines = []
		if port:
			lines.append("Port: %s" % port)
		for key, label, _speak, _status in STATUS_ITEMS:
			lines.append("%s: %s" % (label, self._values.get(key, "not read")))
		if message:
			lines.append(message)
		try:
			self.statusText.SetValue("\r\n".join(lines))
		except RuntimeError:  # dialog closed
			pass

	def readStatus(self):
		"""Read every status item without speaking it (on opening)."""
		self._run([key for key, _l, _s, _c in STATUS_ITEMS], speak=False)

	def check(self, key):
		"""Have the unit speak an item, and show it."""
		self._run([key], speak=True)

	def _run(self, keys, speak):
		if self._busy:
			_speak("Still waiting for the DECtalk Express")
			return
		synth = _activeExpress()
		log.info("DECtalk Express configuration utility: reading %s (synthesizer active: %s)" % (
			", ".join(keys), synth is not None))
		items = [item for item in STATUS_ITEMS if item[0] in keys]
		self._busy = True
		for key in keys:
			self._values[key] = "reading..."
		self._show()

		def worker():
			port = None
			message = None
			try:
				conn, port = self.link.get(synth)
				for key, _label, speakCmd, statusCmd in items:
					answer = conn.query(statusCmd)
					log.info("DECtalk Express: %r answered %r" % (statusCmd, answer))
					self._values[key] = _statusText(key, answer)
					if speak:
						# Last, with a quiet time: commands right after "speak" are garbled.
						conn.sendRaw(speakCmd, settle=_serialio.SPEAK_SETTLE)
			except Exception as e:
				log.error("DECtalk Express: reading the status failed", exc_info=True)
				message = "Could not reach the DECtalk Express: %s" % e
			for key in keys:
				if self._values.get(key) == "reading...":
					self._values[key] = "not read"
			wx.CallAfter(self._done, port, message)

		thread = threading.Thread(target=worker, name="DECtalkExpressStatus")
		thread.daemon = True
		thread.start()

	def _done(self, port, message):
		if not self:  # dialog already closed
			return
		self._busy = False
		self._show(port, message)

	def onUpgrade(self, evt):
		synth = _activeExpress()
		if synth is not None and synth.portName:
			port = synth.portName
		else:
			port = _conf.getPort()
		log.info("DECtalk Express configuration utility: upgrade on %s (synthesizer active: %s)" % (port, synth is not None))
		# The upgrade needs the port to itself.
		self.link.close()
		dlg = UpgradeDialog(self, port)
		result = dlg.ShowModal()
		dlg.Destroy()
		if result == wx.ID_OK:
			# Upgraded: the unit must be restarted, so the utility closes too.
			self.GetTopLevelParent().Close()
			return
		self._values = {}
		self._show(port, "Status not read since the upgrade dialog.")


class UpgradeDialog(wx.Dialog):
	"""Choose the update folder, load the files into the unit's RAM and let it
	write them to flash."""

	def __init__(self, parent, port):
		super().__init__(parent, title="Upgrade DECtalk Express firmware")
		self.port = port
		self._flasher = None
		self._files = None
		self._switched = False  # NVDA was moved off the DECtalk Express
		helper = guiHelper.BoxSizerHelper(self, orientation=wx.VERTICAL)
		helper.addItem(wx.StaticText(self, label=(
			"Loads a new firmware from the folder you choose into the memory of the DECtalk "
			"Express on %s; the unit then writes it to its flash memory. This takes several "
			"minutes. Never turn off the unit while the flash memory is written." % port
		), size=(440, -1)))
		folderRow = guiHelper.BoxSizerHelper(self, orientation=wx.HORIZONTAL)
		self.folderCtrl = folderRow.addLabeledControl("Update &folder:", wx.TextCtrl, size=(300, -1))
		self.browseButton = folderRow.addItem(wx.Button(self, label="&Browse..."))
		helper.addItem(folderRow)
		self.fastCheck = helper.addItem(wx.CheckBox(self, label="Fast &transfer (57600 baud)"))
		self.fastCheck.SetValue(True)
		progressRow = wx.BoxSizer(wx.HORIZONTAL)
		progressRow.Add(wx.StaticText(self, label="&Progress:"), flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
		self.progress = wx.Gauge(self, range=1000, size=(380, -1))
		progressRow.Add(self.progress)
		helper.addItem(progressRow)
		self.statusText = helper.addLabeledControl(
			"Stat&us:", wx.TextCtrl, size=(440, 80),
			style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2,
		)
		buttons = guiHelper.ButtonHelper(wx.HORIZONTAL)
		self.startButton = buttons.addButton(self, label="&Start upgrade")
		self.closeButton = buttons.addButton(self, id=wx.ID_CANCEL, label="&Close")
		helper.addDialogDismissButtons(buttons)
		self.browseButton.Bind(wx.EVT_BUTTON, self.onBrowse)
		self.startButton.Bind(wx.EVT_BUTTON, self.onStart)
		self.closeButton.Bind(wx.EVT_BUTTON, self.onClose)
		self.Bind(wx.EVT_CLOSE, self.onClose)
		self.SetEscapeId(wx.ID_CANCEL)
		border = wx.BoxSizer(wx.VERTICAL)
		border.Add(helper.sizer, flag=wx.ALL, border=guiHelper.BORDER_FOR_DIALOGS)
		self.SetSizerAndFit(border)
		self.CentreOnScreen()
		self.folderCtrl.SetFocus()

	def _status(self, text):
		self.statusText.SetValue(text)

	def onBrowse(self, evt):
		with wx.DirDialog(self, "Folder with the firmware update files",
				defaultPath=self.folderCtrl.GetValue()) as dd:
			if dd.ShowModal() == wx.ID_OK:
				self.folderCtrl.SetValue(dd.GetPath())
				try:
					_flash.findFiles(dd.GetPath())
					self._status("All three update files found.")
				except _flash.FlashError as e:
					self._status(str(e))
		self.folderCtrl.SetFocus()

	def onStart(self, evt):
		try:
			files = _flash.findFiles(self.folderCtrl.GetValue().strip())
		except _flash.FlashError as e:
			gui.messageBox(str(e), self.GetTitle(), wx.OK | wx.ICON_ERROR, self)
			return
		if gui.messageBox(
			"Upgrade the firmware of the DECtalk Express on %s now?\n\n"
			"Make sure the unit is on and has external power. Do not turn it off "
			"until the upgrade is complete." % self.port,
			self.GetTitle(), wx.YES_NO | wx.ICON_WARNING, self,
		) != wx.YES:
			return
		self._files = files
		if _activeExpress() is not None:
			# The driver must let go of the port, and the unit can't speak
			# during the upgrade.
			if not self._switchToFallback():
				gui.messageBox(
					"NVDA could not switch to another synthesizer, so the port can't be freed.",
					self.GetTitle(), wx.OK | wx.ICON_ERROR, self,
				)
				return
			self._switched = True
		for ctrl in (self.folderCtrl, self.browseButton, self.fastCheck, self.startButton):
			ctrl.Disable()
		self.closeButton.SetLabel("&Cancel")
		self._flasher = _flash.Flasher(
			self.port, files, fast=self.fastCheck.GetValue(),
			progress=lambda fraction, text: wx.CallAfter(self._onProgress, fraction, text),
		)
		self._lastText = None
		thread = threading.Thread(target=self._work, name="DECtalkExpressFlash")
		thread.daemon = True
		thread.start()

	def _switchToFallback(self):
		for name in FALLBACK_SYNTHS:
			try:
				if synthDriverHandler.setSynth(name):
					_speak("NVDA uses %s during the firmware upgrade." % synthDriverHandler.getSynth().description)
					return True
			except Exception:
				log.debugWarning("DECtalk Express: could not switch to %s" % name, exc_info=True)
		return False

	def _onProgress(self, fraction, text):
		if self._flasher is None:
			return
		try:
			if fraction is None:
				self.progress.Pulse()
			else:
				self.progress.SetValue(int(fraction * 1000))
			if text != self._lastText:
				self._lastText = text
				self._status(text)
				_speak(text)
			self.closeButton.Enable(self._flasher.canCancel)
		except RuntimeError:
			pass

	def _work(self):
		try:
			self._flasher.run()
			result = ("done", None)
		except _flash.FlashCancelled:
			result = ("cancelled", None)
		except _flash.FlashError as e:
			result = ("failed", e)
		except Exception as e:
			log.exception("DECtalk Express: firmware upgrade failed")
			result = ("failed", e)
		wx.CallAfter(self._finished, *result)

	def _finished(self, outcome, error):
		flasher, self._flasher = self._flasher, None
		self.closeButton.SetLabel("&Close")
		self.closeButton.Enable()
		reselect = ""
		if self._switched:
			reselect = (" When it has spoken its startup message, select DECtalk Express again "
				"with NVDA+Ctrl+S.")
		if outcome == "done":
			self.progress.SetValue(1000)
			text = ("The firmware upgrade is complete. Turn the DECtalk Express off, wait a few "
				"seconds and turn it on again." + reselect)
			icon = wx.ICON_INFORMATION
		elif outcome == "cancelled":
			text = ("The upgrade was cancelled; the flash memory was not changed. Turn the "
				"DECtalk Express off and on again." + reselect)
			icon = wx.ICON_WARNING
		else:
			if flasher is not None and not flasher.canCancel:
				text = ("The upgrade failed while the flash memory was written: %s\n\n"
					"Try the upgrade again before turning the unit off." % error)
			else:
				text = ("The upgrade failed: %s\nThe flash memory was not changed. Turn the "
					"DECtalk Express off and on again." % error) + reselect
			details = getattr(error, "log", "")
			if details:
				log.info("DECtalk Express firmware upgrade log:\n%s" % details)
			icon = wx.ICON_ERROR
		self._status(text)
		gui.messageBox(text, self.GetTitle(), wx.OK | icon, self)
		if outcome == "done":
			self.EndModal(wx.ID_OK)  # onUpgrade then closes the utility
			return
		for ctrl in (self.folderCtrl, self.browseButton, self.fastCheck, self.startButton):
			ctrl.Enable()

	def onClose(self, evt):
		if self._flasher is not None:
			if self._flasher.canCancel:
				self._flasher.cancel()
				self._status("Cancelling...")
			return
		self.EndModal(wx.ID_CLOSE)


class VoiceManagerPanel(wx.Panel):
	"""Lists custom voices; New/Edit open the editor, plus export/import/delete."""

	def __init__(self, parent):
		super().__init__(parent)
		main = wx.BoxSizer(wx.VERTICAL)

		main.Add(wx.StaticText(self, label="Custom &voices:"), flag=wx.ALL, border=8)
		self.voiceList = wx.ListBox(self, size=(340, 180))
		main.Add(self.voiceList, proportion=1, flag=wx.EXPAND | wx.LEFT | wx.RIGHT, border=8)
		self.voiceList.Bind(wx.EVT_LISTBOX_DCLICK, self.onEdit)

		row = wx.BoxSizer(wx.HORIZONTAL)
		self.newButton = wx.Button(self, label="&New...")
		self.editButton = wx.Button(self, label="&Edit...")
		self.deleteButton = wx.Button(self, label="&Delete")
		self.exportButton = wx.Button(self, label="E&xport...")
		self.importButton = wx.Button(self, label="&Import...")
		for b in (self.newButton, self.editButton, self.deleteButton,
				  self.exportButton, self.importButton):
			row.Add(b, flag=wx.RIGHT, border=6)
		main.Add(row, flag=wx.ALL, border=8)

		self.newButton.Bind(wx.EVT_BUTTON, self.onNew)
		self.editButton.Bind(wx.EVT_BUTTON, self.onEdit)
		self.deleteButton.Bind(wx.EVT_BUTTON, self.onDelete)
		self.exportButton.Bind(wx.EVT_BUTTON, self.onExport)
		self.importButton.Bind(wx.EVT_BUTTON, self.onImport)

		self.SetSizerAndFit(main)
		self._refresh()

	def _refresh(self, select=None):
		names = sorted(_voicestore.load())
		self.voiceList.Set(names)
		if names:
			self.voiceList.SetSelection(names.index(select) if select in names else 0)
		for b in (self.editButton, self.deleteButton, self.exportButton):
			b.Enable(bool(names))

	def _selectedName(self):
		i = self.voiceList.GetSelection()
		return self.voiceList.GetString(i) if i != wx.NOT_FOUND else None

	def _syncSynth(self, activate=None):
		synth = _activeExpress()
		if synth is None:
			return
		try:
			synth.refreshVoices()
			if activate is not None:
				synth.voice = _voicestore.CUSTOM_PREFIX + activate
				synth.saveSettings()
		except Exception:
			log.exception("DECtalk Express: could not sync voice with synth")

	def onNew(self, evt):
		dlg = VoiceEditorDialog(self, existing=None)
		if dlg.ShowModal() == wx.ID_OK:
			self._refresh(select=dlg.savedName)
			self._syncSynth(activate=dlg.savedName)
		dlg.Destroy()

	def onEdit(self, evt):
		name = self._selectedName()
		if not name:
			return
		dlg = VoiceEditorDialog(self, existing=name)
		if dlg.ShowModal() == wx.ID_OK:
			self._refresh(select=dlg.savedName)
			self._syncSynth()
		dlg.Destroy()

	def onDelete(self, evt):
		name = self._selectedName()
		if not name:
			return
		if gui.messageBox(
			"Delete the custom voice %s?" % name,
			TITLE, wx.YES_NO | wx.ICON_WARNING, self,
		) != wx.YES:
			return
		synth = _activeExpress()
		vid = _voicestore.CUSTOM_PREFIX + name
		if synth is not None and getattr(synth, "voice", None) == vid:
			try:
				synth.voice = synth._voiceBase()
				synth.saveSettings()
			except Exception:
				log.exception("DECtalk Express: could not reset voice after delete")
		_voicestore.delete(name)
		self._refresh()
		self._syncSynth()

	def onExport(self, evt):
		name = self._selectedName()
		if not name:
			return
		with wx.FileDialog(
			self, "Export DECtalk voice", wildcard="DECtalk voice (*.dtv)|*.dtv",
			defaultFile=name + ".dtv", style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
		) as fd:
			if fd.ShowModal() != wx.ID_OK:
				return
			try:
				_voicestore.exportDtv(name, fd.GetPath())
			except (_voicestore.VoiceStoreError, OSError) as e:
				gui.messageBox(str(e), TITLE, wx.OK | wx.ICON_ERROR, self)

	def onImport(self, evt):
		with wx.FileDialog(
			self, "Import DECtalk voice", wildcard="DECtalk voice (*.dtv)|*.dtv",
			style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
		) as fd:
			if fd.ShowModal() != wx.ID_OK:
				return
			try:
				name = _voicestore.importDtv(fd.GetPath())
			except _voicestore.VoiceStoreError as e:
				gui.messageBox(str(e), TITLE, wx.OK | wx.ICON_ERROR, self)
				return
		self._refresh(select=name)
		self._syncSynth()


class VoiceEditorDialog(wx.Dialog):
	"""One voice: name, base voice and a spin box per [:dv] parameter.
	Test (Alt+T) speaks the current values."""

	def __init__(self, parent, existing=None):
		self.existing = existing
		self.savedName = None
		title = "Edit voice" if existing else "New voice"
		super().__init__(parent, title=title, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)

		record = _voicestore.load().get(existing) if existing else None
		baseId = record["base"] if record else _params.DEFAULT_VOICE
		values = dict(_params.VOICE_DEFAULTS[baseId])
		if record:
			values.update(record["params"])

		main = wx.BoxSizer(wx.VERTICAL)

		top = wx.BoxSizer(wx.HORIZONTAL)
		top.Add(wx.StaticText(self, label="&Name:"), flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
		self.nameCtrl = wx.TextCtrl(self, value=existing or "", size=(180, -1))
		top.Add(self.nameCtrl, flag=wx.RIGHT, border=16)
		top.Add(wx.StaticText(self, label="&Base voice:"), flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=6)
		self._baseIds = list(_params.VOICES)
		self.baseChoice = wx.Choice(
			self, choices=[_params.VOICES[v][1] for v in self._baseIds]
		)
		self.baseChoice.SetSelection(self._baseIds.index(baseId))
		self.baseChoice.Bind(wx.EVT_CHOICE, self.onBaseChanged)
		top.Add(self.baseChoice, flag=wx.ALIGN_CENTER_VERTICAL)
		main.Add(top, flag=wx.ALL, border=8)

		# Spin boxes grouped by category.
		panel = wx.ScrolledWindow(self, size=(420, 340), style=wx.VSCROLL)
		panel.SetScrollRate(0, 12)
		grid = wx.BoxSizer(wx.VERTICAL)
		self.spins = {}
		lastCat = None
		for code, label, category in _params.VOICE_PARAMS:
			if category != lastCat:
				cap = wx.StaticText(panel, label=category)
				f = cap.GetFont()
				f.SetWeight(wx.FONTWEIGHT_BOLD)
				cap.SetFont(f)
				grid.Add(cap, flag=wx.TOP | wx.LEFT, border=8)
				lastCat = category
			lo, hi = _params.VOICE_LIMITS[code]
			line = wx.BoxSizer(wx.HORIZONTAL)
			line.Add(
				wx.StaticText(panel, label="%s (%d\u2013%d):" % (label, lo, hi), size=(240, -1)),
				flag=wx.ALIGN_CENTER_VERTICAL | wx.LEFT, border=16,
			)
			spin = wx.SpinCtrl(panel, min=lo, max=hi, initial=int(values[code]))
			spin.SetToolTip("[:dv %s] \u2014 range %d to %d" % (code, lo, hi))
			line.Add(spin, flag=wx.LEFT, border=6)
			grid.Add(line, flag=wx.TOP, border=2)
			self.spins[code] = spin
		panel.SetSizer(grid)
		main.Add(panel, proportion=1, flag=wx.EXPAND | wx.ALL, border=8)

		btns = wx.BoxSizer(wx.HORIZONTAL)
		self.testButton = wx.Button(self, label="&Test")
		btns.Add(self.testButton, flag=wx.RIGHT, border=12)
		btns.AddStretchSpacer()
		sizer = self.CreateButtonSizer(wx.OK | wx.CANCEL)
		if sizer:
			btns.Add(sizer)
		main.Add(btns, flag=wx.EXPAND | wx.ALL, border=8)

		self.testButton.Bind(wx.EVT_BUTTON, self.onTest)
		self.Bind(wx.EVT_BUTTON, self.onOk, id=wx.ID_OK)

		# Alt+T also works with focus in the spin boxes.
		accel = wx.AcceleratorTable([
			(wx.ACCEL_ALT, ord("T"), self.testButton.GetId()),
		])
		self.SetAcceleratorTable(accel)

		self.SetSizerAndFit(main)
		self.SetMinSize((460, 480))
		self.CentreOnScreen()
		self.nameCtrl.SetFocus()

	def _currentBase(self):
		return self._baseIds[self.baseChoice.GetSelection()]

	def _currentParams(self):
		return {code: spin.GetValue() for code, spin in self.spins.items()}

	def onBaseChanged(self, evt):
		# A new base voice starts from its own values.
		defaults = _params.VOICE_DEFAULTS[self._currentBase()]
		for code, spin in self.spins.items():
			spin.SetValue(defaults[code])

	def onTest(self, evt):
		base, params = self._currentBase(), self._currentParams()
		synth = _activeExpress()
		if synth is not None:
			try:
				synth.previewVoice(base, params)
			except Exception:
				log.exception("DECtalk Express: preview failed")
			return
		# Not the active synth: use the utility's own connection.
		data = (_previewCommands(base, _params.clampAll(params)) + PREVIEW_TEXT + ".").encode("ascii")
		link = ConfigDialog._instance.general.link if ConfigDialog._instance else _Link()

		def worker():
			try:
				conn, _port = link.get(None)
				conn.sendRaw(data)
			except Exception as e:
				log.error("DECtalk Express: preview failed", exc_info=True)
				wx.CallAfter(gui.messageBox, "Could not play the voice: %s" % e, TITLE, wx.OK | wx.ICON_ERROR)

		thread = threading.Thread(target=worker, name="DECtalkExpressPreview")
		thread.daemon = True
		thread.start()

	def onOk(self, evt):
		name = self.nameCtrl.GetValue().strip()
		if not name:
			gui.messageBox("The voice needs a name.", TITLE, wx.OK | wx.ICON_ERROR, self)
			self.nameCtrl.SetFocus()
			return
		existingNames = _voicestore.load()
		if name != self.existing and name in existingNames:
			if gui.messageBox(
				"A custom voice named %s already exists. Overwrite it?" % name,
				TITLE, wx.YES_NO | wx.ICON_WARNING, self,
			) != wx.YES:
				return
		try:
			# Renaming: drop the old record after saving the new one.
			_voicestore.create(name, self._currentBase(), self._currentParams())
			if self.existing and self.existing != name:
				_voicestore.delete(self.existing)
		except _voicestore.VoiceStoreError as e:
			gui.messageBox(str(e), TITLE, wx.OK | wx.ICON_ERROR, self)
			return
		self.savedName = name
		self.EndModal(wx.ID_OK)


# -- settings panel: COM port --

def _portLabel(port, desc):
	if not desc:
		return port
	return desc if port.upper() in desc.upper() else "%s (%s)" % (port, desc)


if SettingsPanel is not None:

	class DECtalkExpressSettingsPanel(SettingsPanel):
		title = "DECtalk Express"

		def makeSettings(self, settingsSizer):
			helper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)
			self._ports = []
			self.portList = helper.addLabeledControl("&COM port:", wx.Choice, choices=[])
			self._fillPorts(_conf.getPort() if _conf.isPortConfigured() else None)
			buttons = guiHelper.ButtonHelper(wx.HORIZONTAL)
			self.findButton = buttons.addButton(self, label="&Find automatically")
			self.testButton = buttons.addButton(self, label="&Test")
			self.refreshButton = buttons.addButton(self, label="&Refresh list")
			helper.addItem(buttons)
			self.findButton.Bind(wx.EVT_BUTTON, self.onFind)
			self.testButton.Bind(wx.EVT_BUTTON, self.onTest)
			self.refreshButton.Bind(wx.EVT_BUTTON, self.onRefresh)

		def _fillPorts(self, select):
			self._ports = list(_conf.listPorts())
			if select and select.upper() not in [p.upper() for p, _d in self._ports]:
				self._ports.insert(0, (select, "not present"))
			if not self._ports:
				self._ports = [("COM%d" % i, "") for i in range(1, 17)]
			self.portList.Set([_portLabel(p, d) for p, d in self._ports])
			index = 0
			for i, (p, _d) in enumerate(self._ports):
				if select and p.upper() == select.upper():
					index = i
			self.portList.SetSelection(index)

		def _selectedPort(self):
			i = self.portList.GetSelection()
			return self._ports[i][0] if 0 <= i < len(self._ports) else None

		def onRefresh(self, evt):
			self._fillPorts(self._selectedPort())
			self.portList.SetFocus()

		def onTest(self, evt):
			port = self._selectedPort()
			if not port:
				return
			text = "This is the DECtalk Express on %s." % port
			synth = _activeExpress()
			try:
				if synth is not None and (synth.portName or "").upper() == port.upper():
					synth.speakRaw(text)
				else:
					_serialio.sendText(port, text)
			except Exception as e:
				gui.messageBox("Could not open %s: %s" % (port, e), "DECtalk Express", wx.OK | wx.ICON_ERROR, self)

		def onFind(self, evt):
			self.findButton.Disable()
			ports = [p for p, _d in self._ports]
			synth = _activeExpress()
			# The driver's own port is in use; the unit answered when it was opened.
			activePort = synth.portName if synth is not None else None
			try:
				import ui
				ui.message("Searching...")
			except Exception:
				pass

			def worker():
				found = None
				for port in ports:
					if (activePort and port.upper() == activePort.upper()) or _serialio.probePort(port):
						found = port
						break
				wx.CallAfter(self._findDone, found)

			thread = threading.Thread(target=worker, name="DECtalkExpressFind")
			thread.daemon = True
			thread.start()

		def _findDone(self, found):
			try:
				self.findButton.Enable()
			except RuntimeError:  # panel already closed
				return
			if found:
				self._fillPorts(found)
				msg = "A DECtalk Express answered on %s." % found
				icon = wx.ICON_INFORMATION
			else:
				msg = "No DECtalk Express answered. Check that it is switched on and connected."
				icon = wx.ICON_WARNING
			gui.messageBox(msg, "DECtalk Express", wx.OK | icon, self)
			self.portList.SetFocus()

		def onSave(self):
			port = self._selectedPort()
			if not port:
				return
			oldPort = _conf.getPort() if _conf.isPortConfigured() else None
			_conf.setPort(port)
			synth = _activeExpress()
			if synth is None or (synth.portName or "").upper() == port.upper():
				return
			try:
				synth.reopen(port)
			except Exception as e:
				if oldPort:
					_conf.setPort(oldPort)
				gui.messageBox(
					"Could not open %s: %s\nThe previous port is still used." % (port, e),
					"DECtalk Express", wx.OK | wx.ICON_ERROR,
				)
