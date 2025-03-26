from __future__ import annotations

import configparser
import time
from typing import Iterable, Tuple

import click
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
BANK_SELECT = 0b00000000
BANK_SELECT_LSB = 0b00100000

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


def _keep_trying(exc, on_error, callable, *args, **kwargs):
    while True:
        try:
            return callable(*args, **kwargs)
        except exc:
            click.secho(on_error, fg="magenta", err=True)
            time.sleep(1)


def resolve_ports(
    cfg: configparser.ConfigParser,
) -> tuple[dict[str, MidiIn], dict[str, MidiOut]]:
    inputs = {}
    outputs = {}
    for name, section in cfg.items():
        port_name = section.get("port-name")
        if not port_name:
            continue
        if name.startswith("from-"):
            if port_name in inputs:
                continue
            inputs[port_name] = _keep_trying(
                ValueError,
                f"{name} port {port_name} not connected",
                get_input,
                port_name,
                clock_source=True,
            )
        elif name.startswith("to-"):
            if port_name in outputs:
                continue
            outputs[port_name] = _keep_trying(
                ValueError,
                f"{name} port {port_name} not connected",
                get_output,
                port_name,
            )
        else:
            click.secho(f"port-name in an unsupported section {name}", err=True)
    return inputs, outputs


get_out_port = get_output


def silence(
    port: MidiOut, *, stop: bool = True, channels: Iterable[int] = ALL_CHANNELS
) -> None:
    if stop:
        port.send_message([STOP])
    for channel in channels:
        port.send_message([CONTROL_CHANGE | channel, SUSTAIN_PEDAL, 0])
        port.send_message([CONTROL_CHANGE | channel, ALL_NOTES_OFF, 0])


def float_to_msb_lsb(value: float) -> tuple[int, int]:
    value = max(0.0, min(1.0, value))
    int_value = int(round(value * 16383))
    msb = (int_value >> 7) & 0x7F
    lsb = int_value & 0x7F
    return msb, lsb
