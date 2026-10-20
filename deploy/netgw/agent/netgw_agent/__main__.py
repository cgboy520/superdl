"""netgw agent entry: `python3 -m netgw_agent [--check] [SPEC]` (default /etc/netgw/spec.json).

Order: resolver → tunnel (prepare retried until it succeeds, then daemons) → relays and SOCKS →
health endpoint; every 5 s readiness is re-evaluated and routes re-asserted. SIGTERM stops the
daemons and exits 0.
"""

import argparse
import asyncio
import contextlib
import os
import signal
import sys

from netgw_agent import relay, render, run, status
from netgw_agent.spec import Spec, SpecError, load

TICK_SECONDS = 5.0


async def _prepare(driver: run.Driver, stop: asyncio.Event) -> bool:
    delay = 2.0
    while not stop.is_set():
        try:
            await asyncio.to_thread(driver.prepare)
            return True
        except (OSError, run.CommandError, TimeoutError) as exc:
            run.log(f"prepare failed ({exc}); retrying in {delay:.0f}s")
        try:
            await asyncio.wait_for(stop.wait(), delay)
        except TimeoutError:
            delay = min(delay * 2, 60.0)
    return False


def _tick(spec: Spec, driver: run.Driver, sup: run.Supervisor, state: relay.State) -> None:
    up = driver.healthy(sup)
    if up != state.tunnel_up:
        run.log(f"tunnel {'up' if up else 'down'}")
    state.tunnel_up = up
    target = driver.route_target()
    if target is not None:
        try:
            run.ensure_routes(spec, target)
        except run.CommandError as exc:
            run.log(f"routes: {exc}")
    driver.maintain(up)


async def amain(spec: Spec) -> int:
    os.makedirs(render.RUN_DIR, mode=0o700, exist_ok=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    sup = run.Supervisor()
    sup.start(run.dnsmasq_proc(spec))
    state = relay.State()
    servers = [await status.start(state)]
    driver = run.DRIVERS[spec.protocol](spec)
    run.log(f"gateway {spec.gateway} protocol {spec.protocol}")
    if await _prepare(driver, stop):
        for proc in driver.processes():
            sup.start(proc)
        servers += await relay.start(spec, state)
    while not stop.is_set():
        try:
            await asyncio.to_thread(_tick, spec, driver, sup, state)
        except (OSError, run.CommandError, TimeoutError) as exc:
            run.log(f"tick: {exc}")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), TICK_SECONDS)
    run.log("stopping")
    for server in servers:
        server.close()
    await sup.stop()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="netgw_agent")
    parser.add_argument("spec", nargs="?", default="/etc/netgw/spec.json")
    parser.add_argument("--check", action="store_true", help="validate the spec and exit")
    args = parser.parse_args()
    try:
        spec = load(args.spec)
    except (OSError, ValueError, SpecError) as exc:
        print(f"netgw: invalid spec: {exc}", file=sys.stderr)
        return 2
    if args.check:
        print("netgw: spec ok", file=sys.stderr)
        return 0
    return asyncio.run(amain(spec))


if __name__ == "__main__":
    sys.exit(main())
