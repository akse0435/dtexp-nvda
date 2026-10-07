# DECtalk Express driver for NVDA

## What is this

This NVDA add-on adds support for using a DECtalk Express hardware speech synthesizer (or another DECtalk unit in RS-232 mode) to NVDA. It supports changing of voices, rate, pitch, inflection, and pauses from the voice settings dialog in NVDA. It also comes with a configuration utility where you can create custom voices, check firmware and battery status, and even upgrade the firmware of your DECtalk Express.

Please Note: this NVDA add-on has been coded with the help of AI, so not everything might be perfect, however all testing of the add-on has been done by myself with real hardware. All contributions and feedback are very welcome.

## Getting started

1. Install this add-on and restart NVDA.
2. Open NVDA menu, Preferences, Settings, category **DECtalk Express**, and choose the COM port. **Find automatically** searches all ports for the unit; **Test** speaks a test sentence on the chosen port. Press OK.
3. Select DECtalk Express in the synthesizer dialog (NVDA+Ctrl+S).

The port is always opened at 9600 baud, 8 data bits, no parity, 1 stop bit, XON/XOFF flow control, and NVDA gives an error if there's no DECtalk Express on the selected port.

## Voice settings

The following settings can be changed in the voice settings dialog:

- **Voice**: Perfect Paul, Beautiful Betty, Huge Harry, Frail Frank, Doctor Dennis, Kit the Kid, Uppity Ursula, Rough Rita, Whispering Wendy, Variable Val, and custom voices.
- **Rate**: 0 % is 75 and 100 % is 650 words per minute.
- **Pitch**: average pitch; 0 % is 50 Hz and 100 % is 350 Hz.
- **Inflection**: pitch range; 0 % is 0 and 100 % is 250 % of normal.
- **Sentence pause** and **Comma pause**: milliseconds added to the normal pause (470 ms after a period and 95 ms after a comma on the DECtalk Express).
- **Split mixed-case words**: "DECTalk" is spoken as "DEC Talk".
- **Allow inline DECtalk commands in spoken text**: DECtalk's own inline commands are passed to the unit. Unpaired square brackets are always removed.

Choosing a voice sets pitch and inflection to that voice's own values, for example 24 % pitch for Paul (122 Hz).

## DECtalk Express Configuration Utility

NVDA menu, Tools, DECtalk Express Configuration Utility. It has two tabs.

### General

#### Firmware version and power status

When the utility opens, it asks the unit for its firmware version and power status and shows the answers in the Status field. **Check firmware version** and **Check battery status** update the field and then make the unit speak the information.

#### Firmware upgrade

**Upgrade firmware** lets you flash a new firmware to the DECtalk Express, useful if you want to try out another version than the one currently installed in your unit. Make sure NVDA is using another speech synthesizer before starting this process.

1. When the button is activated, you are first asked to choose the folder with the update files (mon.hex, fastload.hxo and out.flr). Here you can also choose to turn off fast transfer.
2. The files are then loaded into the unit's memory in that order, and the unit then writes the new firmware to its flash memory. The upgrade takes several minutes, and NVDA announces the progress. Until the flash memory is being written, the upgrade can be cancelled without changing the unit. Never turn the unit off while the flash memory is written.
3. When the upgrade is complete, turn the unit off and on again. When the unit has spoken its startup message, select DECtalk Express in the synthesizer dialog.
4. If fast transfer (57600 baud) causes problems, turn it off to use 9600 baud.

### Voice Manager

Here you can create custom voices from a base voice, which gives you access to set every single parameter DECtalk supports. **Test** (Alt+T) makes the selected voice speak a test sentence on the unit. Saved voices appear in the voice list, and each voice can be exported as a .dtv file, to be imported in another NVDA installation.

## Connection check

When the synthesizer is loaded, the add-on sends Ctrl+C and waits for the unit's answer. If the unit is off, not connected, or on another port, NVDA reports that the synthesizer could not be loaded and keeps the current one. The cable must carry data in both directions; the unit's answers are also what NVDA needs for say all.

## Notes

- There is no volume setting yet; use the volume control on the unit.
- Voice defaults are from DECtalk Express firmware version 4.2CD.
- The add-on is configured for US English only, since another language can only be used by installling a firmware with a different language on the unit. Non US characters are simplified to plain ASCII.
