"""A trivial WebSocket <-> UDP bridge for SerialOSC to control Monome devices from a browser.
 
This bridge does not understand the Monome protocol at all, it passes through all
data between SerialOSC and the WebSocket client without any modifications.

By default listens for WebSocket connections on 127.0.0.1:8765. The first expected
message is b"ohai:SERIALOSC_UDP_PORT" where SERIALOSC_UDP_PORT is the UDP port to which
this script should pass through OSC messages sent from the WebSocket.
In response, the bridge sends back b"iaho:BRIDGE_UDP_PORT", which is the bridge's UDP
port where SerialOSC should respond to the bridge. This information is passed to the
WebSocket client so that it can package /sys/host and /sys/port messages correctly and
send them back. They're passed to SerialOSC via UDP and the handshake is complete.

Initially, the WebSocket client should be connecting to SERIALOSC_UDP_PORT 12002, which
is where the serialosc daemon is serving information on Monome devices being connected
or disconnected. Then through sending `/serialosc/notify` to SerialOSC, the WebSocket
client signals they should be notified about connections and disconnections. Those
happen through `/serialosc/device`, `/serialosc/add`, and `/serialosc/remove`.

From there, the WebSocket client code can open a new WebSocket client connection to the
particular device.

OSC protocol details at https://monome.org/docs/serialosc/.
"""

import asyncio
import logging
import sys

import click
import structlog
from websockets.server import serve


class PassthroughProtocol(asyncio.DatagramProtocol):
    def __init__(self, port, ws, log):
        super().__init__()
        self.port = port
        self.loop = asyncio.get_running_loop()
        self.ws = ws
        self.log = log

    async def __aenter__(self):
        transport, protocol = await self.loop.create_datagram_endpoint(
            lambda: self,
            local_addr=("127.0.0.1", 0),
            remote_addr=("127.0.0.1", self.port),
        )
        _my_udp_host, _my_udp_port = transport.get_extra_info("sockname")
        self.log.info(f"Handshake sent back", local_udp_port=_my_udp_port)
        await self.ws.send(f"iaho:{_my_udp_port}".encode())
        return protocol

    async def __aexit__(self, exc_type, exc_value, traceback):
        self.transport.close()

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr=None):
        self.log.debug("udp -> ws", data=data)
        self.loop.create_task(self.ws.send(data))

    def send(self, data, addr=None):
        self.log.debug("ws -> udp", data=data)
        return self.transport.sendto(data, addr=addr)


async def handler(websocket, log):
    log = log.bind(
        client_host=websocket.remote_address[0], client_port=websocket.remote_address[1]
    )
    log.info("WebSocket opened")
    message = await websocket.recv()
    if not isinstance(message, bytes) or not message.startswith(b"ohai:"):
        log.warning("unhandled first WebSocket message", message=message)
        return

    serialosc_udp_port = int(message[len("ohai:") :])
    log.info(
        "Handshake received",
        serialosc_udp_port=serialosc_udp_port,
    )

    async with PassthroughProtocol(serialosc_udp_port, websocket, log) as serialosc:
        log.info("Ready for WebSocket messages")
        async for message in websocket:
            serialosc.send(message)
    log.info("WebSocket closed")


async def async_main(port, log):
    host = "localhost"
    async with serve(lambda websocket: handler(websocket, log), host, port):
        log.info("Serving", server_host=host, server_port=port)
        await asyncio.get_running_loop().create_future()  # run forever


@click.command()
@click.option(
    "--debug",
    is_flag=True,
    help="Display messages sent back and forth. Warning: this slows down the bridge",
)
@click.argument("port", type=int, default=8765)
def main(debug, port):
    """A trivial WebSocket <-> UDP bridge for SerialOSC to control Monome devices."""

    log = structlog.get_logger()
    if not debug:
        structlog.configure(
            wrapper_class=structlog.make_filtering_bound_logger(logging.INFO)
        )
    if not sys.stdout.isatty():
        structlog.configure(
            [
                structlog.processors.TimeStamper(),
                structlog.processors.dict_tracebacks,
                structlog.processors.JSONRenderer(),
            ]
        )
    asyncio.run(async_main(port, log))


if __name__ == "__main__":
    main()
