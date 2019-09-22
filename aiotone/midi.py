from __future__ import annotations

from typing import Tuple

from rtmidi import MidiIn, MidiOut


# MIDI constants
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

# Operations on MIDI constants
STRIP_CHANNEL = 0b11110000


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
