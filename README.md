# aiotone

MIDI processing tools in AsyncIO.

## Installation

```
$ git clone https://github.com/ambv/aiotone
$ python3.8 -m venv /tmp/aiotone
$ source /tmp/aiotone/bin/activate
(aiotone)$ poetry install
```

If you don't have Poetry installed yet, check out
https://python-poetry.org/.


DISCLAIMER: tested on macOS only.


## Usage

### Performing on two Moog Mother 32 synthesizers as one instrument

```
(aiotone)$ python -m aiotone.redblue --help
```

### Sequencing the Novation Circuit + Novation Circuit Mono Station

```
(aiotone)$ python -m aiotone.circuits --help
```