"""Simulate a mainland-China network for install / download tests.

A local HTTP(S) proxy: hosts on the BLOCKED list are black-holed (the CONNECT is
accepted but never answered, like the GFW's silent drop -> the client times
out); everything else is tunnelled normally. Every host the app tries is
logged, so you can see which source each step really used.

    python tools/cn_net_sim.py [port]          # default 8899
    set HTTP_PROXY=http://127.0.0.1:8899 & set HTTPS_PROXY=http://127.0.0.1:8899
    set NO_PROXY=localhost,127.0.0.1
    ...run the install in THAT environment...
"""
import asyncio
import sys
import time

BLOCKED = (
    "pypi.org", "pythonhosted.org",                     # official PyPI
    "huggingface.co", "hf.co", "xethub.hf.co",          # HF (not hf-mirror.com)
    "github.com", "githubusercontent.com",              # GitHub + raw/release assets
    "download.pytorch.org", "googleapis.com", "google.com",
)


def blocked(host):
    host = host.lower()
    return any(host == b or host.endswith("." + b) for b in BLOCKED)


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


async def pipe(r, w):
    try:
        while data := await r.read(65536):
            w.write(data)
            await w.drain()
    except Exception:  # noqa: BLE001
        pass
    finally:
        w.close()


async def handle(cr, cw):
    try:
        head = await cr.readuntil(b"\r\n\r\n")
    except Exception:  # noqa: BLE001
        cw.close()
        return
    first = head.split(b"\r\n", 1)[0].decode("latin-1")
    method, target, _ = first.split(" ", 2)
    if method == "CONNECT":
        host, port = target.rsplit(":", 1)
    else:   # plain http://host[:port]/path
        rest = target.split("://", 1)[1]
        hostport = rest.split("/", 1)[0]
        host, port = (hostport.rsplit(":", 1) + ["80"])[:2] if ":" in hostport else (hostport, "80")
    if blocked(host):
        log(f"BLOCK {host}")
        await asyncio.sleep(3600)          # black hole: never answer
        cw.close()
        return
    log(f"allow {host}")
    try:
        ur, uw = await asyncio.wait_for(asyncio.open_connection(host, int(port)), 20)
    except Exception as e:  # noqa: BLE001
        log(f"FAIL  {host}: {e}")
        cw.close()
        return
    if method == "CONNECT":
        cw.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await cw.drain()
    else:
        uw.write(head)
        await uw.drain()
    await asyncio.gather(pipe(cr, uw), pipe(ur, cw))


async def main(port):
    srv = await asyncio.start_server(handle, "127.0.0.1", port)
    log(f"CN network simulator on 127.0.0.1:{port} (blocking: {', '.join(BLOCKED)})")
    async with srv:
        await srv.serve_forever()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 8899))
