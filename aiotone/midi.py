from __future__ import annotations

from typing import Iterable, Tuple

from rtmidi import MidiIn, MidiOut


# MIDI messages
NOTE_OFF = 0b10000000
NOTE_ON = 0b10010000
POLY_AFTERTOUCH = 0b10100000
CONTROL_CHANGE = 0b10110000
PROGRAM_CHANGE = 0b11000000
CHAN_AFTERTOUCH = 0b11010000
PITCH_BEND = 0b11100000
SYSEX = 0b11110000
SYSEX_RT = 0b11111000
PANIC = 0b11111111
CLOCK = 0b11111000
START = 0b11111010
STOP = 0b11111100
SONG_POSITION = 0b11110010

# MIDI special values (use with CONTROL_CHANGE)
ALL_NOTES_OFF = 0b01111011
MOD_WHEEL = 0b00000001
EXPRESSION_PEDAL = 0b00001011
SUSTAIN_PEDAL = 0b01000000
PORTAMENTO = 0b01000001
PORTAMENTO_TIME = 0b00000101

# Operations on MIDI constants
STRIP_CHANNEL = 0b11110000


ALL_CHANNELS = range(16)


def get_ports(port_name: str, *, clock_source: bool = False) -> Tuple[MidiIn, MidiOut]:
    midi_in = MidiIn()
    midi_out = MidiOut()

    midi_in_ports = midi_in.get_ports()
    midi_out_ports = midi_out.get_ports()
    try:
        midi_in.open_port(midi_in_ports.index(port_name))
    except ValueError:
        raise ValueError(port_name) from None

    if clock_source:
        midi_in.ignore_types(timing=False)
    try:
        midi_out.open_port(midi_out_ports.index(port_name))
    except ValueError:
        raise ValueError(port_name) from None

    return midi_in, midi_out


def get_out_port(port_name: str) -> MidiOut:
    midi_out = MidiOut()
    midi_out_ports = midi_out.get_ports()
    try:
        midi_out.open_port(midi_out_ports.index(port_name))
    except ValueError:
        raise ValueError(port_name) from None

    return midi_out


def silence(
    port: MidiOut, *, stop: bool = True, channels: Iterable[int] = ALL_CHANNELS
) -> None:
    if stop:
        port.send_message([STOP])
    for channel in channels:
        port.send_message([CONTROL_CHANGE | channel, ALL_NOTES_OFF, 0])
