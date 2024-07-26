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
POLY_AFTERTOUCH = 0b10100000
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
ALL_SOUND_OFF = 0b01111000
MOD_WHEEL = 0b00000001
MOD_WHEEL_LSB = 0b00100001
FOOT_PEDAL = 0b00000100
EXPRESSION_PEDAL = 0b00001011
EXPRESSION_PEDAL_LSB = 0b00101011
SUSTAIN_PEDAL = 0b01000000
PORTAMENTO = 0b01000001
PORTAMENTO_TIME = 0b00000101

# Operations on MIDI constants
STRIP_CHANNEL = 0b11110000
GET_CHANNEL = 0b00001111


ALL_CHANNELS = range(16)


def get_ports(port_name: str, *, clock_source: bool = False) -> Tuple[MidiIn, MidiOut]:
    return get_input(port_name, clock_source=clock_source), get_output(port_name)


def get_input(port_name: str, *, clock_source: bool = False) -> MidiIn:
    midi_in = MidiIn()
    midi_in_ports = midi_in.get_ports()
    try:
        midi_in.open_port(midi_in_ports.index(port_name))
    except ValueError:
        raise ValueError(port_name) from None

    if clock_source:
        midi_in.ignore_types(timing=False)

    return midi_in


def get_output(port_name: str) -> MidiOut:
    midi_out = MidiOut()
    midi_out_ports = midi_out.get_ports()
    try:
        midi_out.open_port(midi_out_ports.index(port_name))
    except ValueError:
        raise ValueError(port_name) from None

    return midi_out


get_out_port = get_output


def silence(
    port: MidiOut, *, stop: bool = True, channels: Iterable[int] = ALL_CHANNELS
) -> None:
    if stop:
        port.send_message([STOP])
    for channel in channels:
        port.send_message([CONTROL_CHANGE | channel, SUSTAIN_PEDAL, 0])
        port.send_message([CONTROL_CHANGE | channel, ALL_NOTES_OFF, 0])
