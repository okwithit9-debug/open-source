#!/usr/bin/env python3
"""127.0.0.1:8001 -> LLM_LAN_IP:8001 TCP relay so Docker (host.docker.internal) can reach Spark Tabby.
Does not touch Tabby. Localhost bind only. Set LLM_LAN_IP to the Tabby host."""
import asyncio, os
LISTEN=("127.0.0.1", 8001); TARGET=(os.environ.get("LLM_LAN_IP", "llm.example.com"), 8001)
async def pipe(r, w):
    try:
        while (d := await r.read(65536)): w.write(d); await w.drain()
    except Exception: pass
    finally:
        try: w.close()
        except Exception: pass
async def handle(cr, cw):
    try: tr, tw = await asyncio.open_connection(*TARGET)
    except Exception as e:
        cw.close(); return
    await asyncio.gather(pipe(cr, tw), pipe(tr, cw))
async def main():
    srv = await asyncio.start_server(handle, *LISTEN); print("relay up", LISTEN, "->", TARGET, flush=True)
    async with srv: await srv.serve_forever()
asyncio.run(main())
