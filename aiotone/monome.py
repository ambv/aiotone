# pymonome - library for interfacing with monome devices
#
# Copyright (c) 2011-2019 Artem Popov <artfwo@gmail.com>
# Copyright (c) 2023 Łukasz Langa <lukasz@langa.pl>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import asyncio
import itertools
import re
import sys

from aiotone import aiosc


def pack_row(row):
    return (
        row[7] << 7
        | row[6] << 6
        | row[5] << 5
        | row[4] << 4
        | row[3] << 3
        | row[2] << 2
        | row[1] << 1
        | row[0]
    )


class Event:
    def __init__(self):
        self.handlers = set()

    def add_handler(self, handler):
        self.handlers.add(handler)

    def remove_handler(self, handler):
        self.handlers.discard(handler)

    def dispatch(self, *args, **kwargs):
        for handler in self.handlers:
            handler(*args, **kwargs)


class Device(aiosc.OSCProtocol):
    def __init__(self, prefix="monome"):
        super().__init__()

        self.add_handler("/sys/disconnect", self._on_sys_disconnect)
        self.add_handler("/sys/{id,size,host,port,prefix,rotation}", self._on_sys_info)

        self.connected = False
        self.transport = None

        self.prefix = prefix

        self.ready_event = Event()
        self.disconnect_event = Event()

        self._reset_info_properties()

    def _reset_info_properties(self):
        self.id = None
        self.width = None
        self.height = None
        self.rotation = None

    def _info_properties_set(self):
        return all(
            x is not None for x in [self.id, self.width, self.height, self.rotation]
        )

    def _on_sys_disconnect(self, addr, path, *args):
        self.disconnect()

    def _on_sys_info(self, addr, path, *args):
        if path == "/sys/id":
            self.id = args[0]
        elif path == "/sys/size":
            self.width, self.height = (args[0], args[1])
        elif path == "/sys/rotation":
            self.rotation = args[0]

        if self._info_properties_set():
            self.connected = True
            self.ready_event.dispatch()

    def connection_made(self, transport):
        super().connection_made(transport)
        self.host, self.port = transport.get_extra_info("sockname")

        self.send("/sys/host", self.host)
        self.send("/sys/port", self.port)
        self.send("/sys/prefix", self.prefix)
        self.send("/sys/info/id", self.host, self.port)
        self.send("/sys/info/size", self.host, self.port)
        self.send("/sys/info/rotation", self.host, self.port)

    async def connect(self, host, port, loop=None):
        if self.transport is not None and not self.transport.is_closing():
            self.disconnect()

        if loop is None:
            if sys.version_info >= (3, 7):
                loop = asyncio.get_running_loop()
            else:
                loop = asyncio.get_event_loop()

        transport, protocol = await loop.create_datagram_endpoint(
            lambda: self, local_addr=("127.0.0.1", 0), remote_addr=(host, port)
        )

    def disconnect(self):
        self.disconnect_event.dispatch()
        self._reset_info_properties()
        self.transport.close()
        self.connected = False


class Grid(Device):
    def __init__(self, prefix="monome"):
        super().__init__(prefix)

        self.add_handler("/*/grid/key", self._on_grid_key)
        self.add_handler("/*/tilt", self._on_tilt)

        self.key_event = Event()
        self.tilt_event = Event()
        self.varibright = True

        self.ready_event.add_handler(self._set_varibright)

    def _set_varibright(self):
        if not re.match(r"^m\d+$", self.id, flags=re.IGNORECASE):
            self.varibright = False

    def _on_grid_key(self, addr, path, x, y, s):
        self.key_event.dispatch(x, y, s)

    def _on_tilt(self, addr, path, n, x, y, z):
        self.tilt_event.dispatch(n, x, y, z)

    def led_set(self, x, y, s):
        self.send("/{}/grid/led/set".format(self.prefix), x, y, s)

    def led_all(self, s):
        self.send("/{}/grid/led/all".format(self.prefix), s)

    def led_map(self, x_offset, y_offset, data):
        args = [pack_row(data[i]) for i in range(8)]
        self.send("/{}/grid/led/map".format(self.prefix), x_offset, y_offset, *args)

    def led_row(self, x_offset, y, data):
        args = [pack_row(data[i * 8 : (i + 1) * 8]) for i in range(len(data) // 8)]
        self.send("/{}/grid/led/row".format(self.prefix), x_offset, y, *args)

    def led_col(self, x, y_offset, data):
        args = [pack_row(data[i * 8 : (i + 1) * 8]) for i in range(len(data) // 8)]
        self.send("/{}/grid/led/col".format(self.prefix), x, y_offset, *args)

    def led_intensity(self, i):
        self.send("/{}/grid/led/intensity".format(self.prefix), i)

    def led_level_set(self, x, y, l):
        if self.varibright:
            self.send("/{}/grid/led/level/set".format(self.prefix), x, y, l)
        else:
            self.led_set(x, y, l >> 3 & 1)

    def led_level_all(self, l):
        if self.varibright:
            self.send("/{}/grid/led/level/all".format(self.prefix), l)
        else:
            self.led_all(l >> 3 & 1)

    def led_level_map(self, x_offset, y_offset, data):
        if self.varibright:
            args = itertools.chain(*data)
            self.send(
                "/{}/grid/led/level/map".format(self.prefix), x_offset, y_offset, *args
            )
        else:
            self.led_map(
                x_offset, y_offset, [[l >> 3 & 1 for l in row] for row in data]
            )

    def led_level_map_raw(self, x_offset, y_offset, data):
        self.send(
            "/{}/grid/led/level/map".format(self.prefix), x_offset, y_offset, data
        )

    def led_level_row(self, x_offset, y, data):
        if self.varibright:
            self.send("/{}/grid/led/level/row".format(self.prefix), x_offset, y, *data)
        else:
            self.led_row(x_offset, y, [l >> 3 & 1 for l in data])

    def led_level_col(self, x, y_offset, data):
        if self.varibright:
            self.send("/{}/grid/led/level/col".format(self.prefix), x, y_offset, *data)
        else:
            self.led_col(x, y_offset, [l >> 3 & 1 for l in data])

    def tilt_set(self, n, s):
        self.send("/{}/tilt/set".format(self.prefix), n, s)


class Arc(Device):
    def __init__(self, prefix="monome"):
        super().__init__(prefix)

        self.add_handler("/*/enc/delta", self._on_enc_delta)
        self.add_handler("/*/enc/key", self._on_enc_key)

        self.delta_event = Event()
        self.key_event = Event()

    def _on_enc_delta(self, addr, path, ring, delta):
        self.delta_event.dispatch(ring, delta)

    def _on_enc_key(self, addr, path, n, s):
        self.key_event.dispatch(n, s)

    def ring_set(self, n, x, l):
        self.send("/{}/ring/set".format(self.prefix), n, x, l)

    def ring_all(self, n, l):
        self.send("/{}/ring/all".format(self.prefix), n, l)

    def ring_map(self, n, data):
        self.send("/{}/ring/map".format(self.prefix), n, *data)

    def ring_map_raw(self, n, data):
        self.send("/{}/ring/map".format(self.prefix), n, data)

    def ring_range(self, n, x1, x2, l):
        self.send("/{}/ring/range".format(self.prefix), n, x1, x2, l)


class SerialOsc(aiosc.OSCProtocol):
    def __init__(self, loop=None, autoconnect_app=None):
        super().__init__(
            handlers={
                "/serialosc/device": self._on_serialosc_device,
                "/serialosc/add": self._on_serialosc_add,
                "/serialosc/remove": self._on_serialosc_remove,
            }
        )

        self.device_added_event = Event()
        self.device_removed_event = Event()

    def connection_made(self, transport):
        super().connection_made(transport)
        self.host, self.port = transport.get_extra_info("sockname")

        self.send("/serialosc/list", self.host, self.port)
        self.send("/serialosc/notify", self.host, self.port)

    async def connect(self, loop=None):
        if loop is None:
            if sys.version_info >= (3, 7):
                loop = asyncio.get_running_loop()
            else:
                loop = asyncio.get_event_loop()

        transport, protocol = await loop.create_datagram_endpoint(
            lambda: self, local_addr=("127.0.0.1", 0), remote_addr=("127.0.0.1", 12002)
        )

    def _on_serialosc_device(self, addr, path, id, type, port):
        type = type.strip()  # remove trailing spaces for arcs
        self.device_added_event.dispatch(id, type, port)

    def _on_serialosc_add(self, addr, path, id, type, port):
        type = type.strip()  # remove trailing spaces for arcs
        self.device_added_event.dispatch(id, type, port)
        self.send("/serialosc/notify", self.host, self.port)

    def _on_serialosc_remove(self, addr, path, id, type, port):
        type = type.strip()  # remove trailing spaces for arcs
        self.device_removed_event.dispatch(id, type, port)
        self.send("/serialosc/notify", self.host, self.port)


class GridApp:
    def __init__(self, grid=None):
        if grid is None:
            grid = Grid()

        self.set_grid(grid)

    def set_grid(self, grid):
        self.grid = grid
        self.grid.ready_event.add_handler(self.on_grid_ready)
        self.grid.disconnect_event.add_handler(self.on_grid_disconnect)
        self.grid.key_event.add_handler(self.on_grid_key)
        self.grid.tilt_event.add_handler(self.on_tilt)

    def on_grid_ready(self):
        pass

    def on_grid_disconnect(self):
        pass

    def on_grid_key(self, x, y, s):
        pass

    def on_tilt(self, n, x, y, z):
        pass


class ArcApp:
    def __init__(self, arc=None):
        if arc is None:
            arc = Arc()

        self.set_arc(arc)

    def set_arc(self, arc):
        self.arc = arc
        self.arc.ready_event.add_handler(self.on_arc_ready)
        self.arc.disconnect_event.add_handler(self.on_arc_disconnect)
        self.arc.delta_event.add_handler(self.on_arc_delta)
        self.arc.key_event.add_handler(self.on_arc_key)

    def on_arc_ready(self):
        pass

    def on_arc_disconnect(self):
        pass

    def on_arc_delta(self, ring, delta):
        pass

    def on_arc_key(self, ring, s):
        pass


class GridBuffer:
    def __init__(self, width, height):
        self.levels = [[0 for col in range(width)] for row in range(height)]
        self.width = width
        self.height = height

    def __and__(self, other):
        result = GridBuffer(self.width, self.height)
        for row in range(self.height):
            for col in range(self.width):
                result.levels[row][col] = self.levels[row][col] & other.levels[row][col]
        return result

    def __xor__(self, other):
        result = GridBuffer(self.width, self.height)
        for row in range(self.height):
            for col in range(self.width):
                result.levels[row][col] = self.levels[row][col] ^ other.levels[row][col]
        return result

    def __or__(self, other):
        result = GridBuffer(self.width, self.height)
        for row in range(self.height):
            for col in range(self.width):
                result.levels[row][col] = self.levels[row][col] | other.levels[row][col]
        return result

    def led_set(self, x, y, s):
        self.led_level_set(x, y, s * 15)

    def led_all(self, s):
        self.led_level_all(s * 15)

    def led_map(self, x_offset, y_offset, data):
        for r, row in enumerate(data):
            self.led_row(x_offset, y_offset + r, row)

    def led_row(self, x_offset, y, data):
        for x, s in enumerate(data):
            self.led_set(x_offset + x, y, s)

    def led_col(self, x, y_offset, data):
        for y, s in enumerate(data):
            self.led_set(x, y_offset + y, s)

    def led_level_set(self, x, y, l):
        if x < self.width and y < self.height:
            self.levels[y][x] = l

    def led_level_all(self, l):
        for x in range(self.width):
            for y in range(self.height):
                self.levels[y][x] = l

    def led_level_map(self, x_offset, y_offset, data):
        for r, row in enumerate(data):
            self.led_level_row(x_offset, y_offset + r, row)

    def led_level_row(self, x_offset, y, data):
        if y < self.height:
            for x, l in enumerate(data[: self.width - x_offset]):
                self.levels[y][x + x_offset] = l

    def led_level_col(self, x, y_offset, data):
        if x < self.width:
            for y, l in enumerate(data[: self.height - y_offset]):
                self.levels[y + y_offset][x] = l

    def get_level_map(self, x_offset, y_offset):
        map = []
        for y in range(y_offset, y_offset + 8):
            row = [self.levels[y][col] for col in range(x_offset, x_offset + 8)]
            map.append(row)
        return map

    def render(self, grid):
        for x_offset in [i * 8 for i in range(self.width // 8)]:
            for y_offset in [i * 8 for i in range(self.height // 8)]:
                grid.led_level_map(
                    x_offset, y_offset, self.get_level_map(x_offset, y_offset)
                )


class ArcBuffer:
    def __init__(self, rings):
        self.rings = rings
        self.levels = [[0 for i in range(64)] for ring in range(rings)]

    def __and__(self, other):
        rings = len(self.levels)
        result = ArcBuffer(rings)
        for ring in range(rings):
            for x in range(64):
                result.levels[ring][x] = self.levels[ring][x] & other.levels[ring][x]
        return result

    def __xor__(self, other):
        result = GridBuffer(self.width, self.height)
        for row in range(self.height):
            for col in range(self.width):
                result.levels[row][col] = self.levels[row][col] ^ other.levels[row][col]
        return result

    def __or__(self, other):
        result = GridBuffer(self.width, self.height)
        for row in range(self.height):
            for col in range(self.width):
                result.levels[row][col] = self.levels[row][col] | other.levels[row][col]
        return result

    def ring_set(self, n, x, l):
        self.levels[n][x] = l

    def ring_all(self, n, l):
        self.levels[n] = [l] * 64

    def ring_map(self, n, data):
        self.levels[n] = data

    def ring_range(self, n, x1, x2, l):
        for i in range(x1, x2 + 1):
            self.levels[n][i] = l

    def render(self, arc):
        for i in range(self.rings):
            arc.ring_map(i, self.levels[i])


class GridPage:
    def __init__(self, manager):
        self.manager = manager
        self.buffer = None

        self.ready_event = Event()
        self.disconnect_event = Event()
        self.key_event = Event()
        self.tilt_event = Event()

    def manager_ready(self):
        self.id = "grid_page"
        self.width = self.manager.grid.width
        self.height = self.manager.grid.height
        self.rotation = self.manager.grid.rotation

        self.buffer = GridBuffer(self.width, self.height)
        self.ready_event.dispatch()

    def manager_disconnect(self):
        self.disconnect_event.dispatch()

    @property
    def active(self):
        return self is self.manager.current_page

    def led_set(self, x, y, s):
        self.buffer.led_set(x, y, s)
        if self.active:
            self.manager.grid.led_set(x, y, s)

    def led_all(self, s):
        self.buffer.led_all(s)
        if self.active:
            self.manager.grid.led_all(s)

    def led_map(self, x_offset, y_offset, data):
        self.buffer.led_map(x_offset, y_offset, data)
        if self.active:
            self.manager.grid.led_map(x_offset, y_offset, data)

    def led_row(self, x_offset, y, data):
        self.buffer.led_row(x_offset, y, data)
        if self.active:
            self.manager.grid.led_row(x_offset, y, data)

    def led_col(self, x, y_offset, data):
        self.buffer.led_col(x, y_offset, data)
        if self.active:
            self.manager.grid.led_col(x, y_offset, data)

    def led_intensity(self, i):
        self.manager.grid.led_intensity(i)

    def led_level_set(self, x, y, l):
        self.buffer.led_level_set(x, y, l)
        if self.active:
            self.manager.grid.led_level_set(x, y, l)

    def led_level_all(self, l):
        self.buffer.led_level_all(l)
        if self.active:
            self.manager.grid.led_level_all(l)

    def led_level_map(self, x_offset, y_offset, data):
        self.buffer.led_level_map(x_offset, y_offset, data)
        if self.active:
            self.manager.grid.led_level_map(x_offset, y_offset, data)

    def led_level_row(self, x_offset, y, data):
        self.buffer.led_level_row(x_offset, y, data)
        if self.active:
            self.manager.grid.led_level_row(x_offset, y, data)

    def led_level_col(self, x, y_offset, data):
        self.buffer.led_level_col(x, y_offset, data)
        if self.active:
            self.manager.grid.led_level_col(x, y_offset, data)


class GridPageManager(GridApp):
    def __init__(self, num_pages=1):
        super().__init__()

        self.pages = [GridPage(self) for i in range(num_pages)]
        self.set_current_page(0)

    def on_grid_ready(self):
        for page in self.pages:
            page.manager_ready()

    def on_grid_key(self, x, y, s):
        self.current_page.key_event.dispatch(x, y, s)

    def on_grid_disconnect(self):
        for page in self.pages:
            page.manager_disconnect()

    def set_current_page(self, index):
        self.current_page = self.pages[index]
        if self.current_page.buffer:
            self.current_page.buffer.render(self.grid)


class SeqGridPageManager(GridPageManager):
    def __init__(self, num_pages=1, switch_button=(-1, -1)):
        super().__init__(num_pages)
        self._switch_button = switch_button

    def on_grid_ready(self):
        super().on_grid_ready()
        switch_x, switch_y = self._switch_button

        self._switch_x = self.grid.width + switch_x if switch_x < 0 else switch_x
        self._switch_y = self.grid.height + switch_y if switch_y < 0 else switch_y

    def on_grid_key(self, x, y, s):
        # TODO: bring back presses from pymonome 0.8
        if x == self._switch_x and y == self._switch_y and s == 1:
            self.set_current_page(
                (self.pages.index(self.current_page) + 1) % len(self.pages)
            )
        else:
            super().on_grid_key(x, y, s)


class SumGridPageManager(GridPageManager):
    def __init__(self, num_pages=1, switch_button=(-1, -1), **kwargs):
        super().__init__(num_pages)
        self._switch_button = switch_button
        self._selected_page_index = -1
        self._presses = set()

    def on_grid_ready(self):
        super().on_grid_ready()
        switch_x, switch_y = self._switch_button

        self._switch_x = self.grid.width + switch_x if switch_x < 0 else switch_x
        self._switch_y = self.grid.height + switch_y if switch_y < 0 else switch_y

    def on_grid_key(self, x, y, s):
        if not self._presses and x == self._switch_x and y == self._switch_y:
            if s == 1:
                self._selected_page_index = self.pages.index(self.current_page)
                # TODO: implement proper setter for this case
                self.current_page = None
                self.display_chooser()
            else:
                self.set_current_page(self._selected_page_index)
            return
        # handle regular buttons
        if self.current_page is None:
            if x < len(self.pages):
                self._selected_page_index = x
                self.display_chooser()
            return

        if s == 1:
            self._presses.add((x, y))
        else:
            self._presses.discard((x, y))

        super().on_grid_key(x, y, s)

    def display_chooser(self):
        self.grid.led_all(0)
        page_row = [1 if i < len(self.pages) else 0 for i in range(self.grid.width)]
        self.grid.led_row(0, self.grid.height - 1, page_row)
        self.grid.led_col(self._selected_page_index, 0, [1] * self.grid.height)


class GridSection:
    def __init__(self, size, offset):
        self.splitter = None

        self.section_width = size[0]
        self.section_height = size[1]
        self.x_offset = offset[0]
        self.y_offset = offset[1]

        self.ready_event = Event()
        self.disconnect_event = Event()
        self.key_event = Event()
        self.tilt_event = Event()

    def splitter_ready(self):
        self.width = self.section_width
        self.height = self.section_height
        self.rotation = 0
        self.ready_event.dispatch()

    def splitter_disconnect(self):
        self.disconnect_event.dispatch()

    def led_set(self, x, y, s):
        if x < self.section_width and y < self.section_height:
            self.splitter.grid.led_set(x + self.x_offset, y + self.y_offset, s)

    def led_all(self, s):
        # TODO: fix map
        data = [[s for col in range(8)] for row in range(8)]
        self.splitter.grid.led_map(self.x_offset, self.y_offset, data)

    def led_map(self, x_offset, y_offset, data):
        self.splitter.grid.led_map(
            self.x_offset + x_offset, self.y_offset + y_offset, data
        )

    def led_row(self, x_offset, y, data):
        data = data[: self.section_width]
        self.splitter.grid.led_row(self.x_offset + x_offset, self.y_offset + y, data)

    def led_col(self, x, y_offset, data):
        data = data[: self.section_height]
        self.splitter.grid.led_col(self.x_offset + x, self.y_offset + y_offset, data)

    def led_intensity(self, i):
        self.splitter.grid.led_intensity(i)

    def led_level_set(self, x, y, l):
        if x < self.section_width and y < self.section_height:
            self.splitter.grid.led_level_set(self.x_offset + x, self.y_offset + y, l)

    def led_level_all(self, l):
        data = [[l for col in range(8)] for row in range(8)]
        self.splitter.grid.led_map(self.x_offset, self.y_offset, data)

    def led_level_map(self, x_offset, y_offset, data):
        self.splitter.grid.led_level_map(
            self.x_offset + x_offset, self.y_offset + y_offset, data
        )

    def led_level_row(self, x_offset, y, data):
        data = data[: self.section_width]
        self.splitter.grid.led_level_row(
            self.x_offset + x_offset, self.y_offset + y, data
        )

    def led_level_col(self, x, y_offset, data):
        data = data[: self.section_height]
        self.splitter.grid.led_level_col(
            self.x_offset + x, self.y_offset + y_offset, data
        )


class GridSplitter(GridApp):
    def __init__(self, sections):
        super().__init__()
        self._sections = sections
        for section in self._sections:
            section.splitter = self

    def on_grid_ready(self):
        for section in self._sections:
            section.splitter_ready()

    def on_grid_disconnect(self):
        for section in self._sections:
            section.splitter_disconnect()

    def on_grid_key(self, x, y, s):
        for section in self._sections:
            if (
                section.x_offset <= x < section.x_offset + section.section_width
                and section.y_offset <= y < section.y_offset + section.section_height
            ):
                section.key_event.dispatch(
                    x - section.x_offset, y - section.y_offset, s
                )
