"""Command line interface: `quakerisk <command>` (or `python -m quakerisk <command>`)."""
import argparse
import json
import os

STEPS = ("exposure", "vulnerability", "engine", "web")


def _run_step(name):
    if name == "exposure":
        from .exposure import build
    elif name == "vulnerability":
        from .vulnerability import build_mapping as build
    elif name == "engine":
        from .engine_data import prepare as build
    else:
        from .webdata import build
    print(f"== {name}")
    build()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="quakerisk", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("download", help="download the open input data into data/raw")
    p.add_argument("--force", action="store_true", help="download again even if the files exist")

    sub.add_parser("build", help="run the whole pipeline: " + " -> ".join(STEPS))
    for step in STEPS:
        sub.add_parser(step, help=f"run only the '{step}' step of the pipeline")

    p = sub.add_parser("eventset", help="sample a stochastic event set from the ESHM20 source model (OpenQuake)")
    p.add_argument("--years", type=int, default=20000, help="simulated years (a multiple of --samples)")
    p.add_argument("--samples", type=int, default=100, help="logic-tree paths; each simulates years/samples years")
    p.add_argument("--name", default="eventset")

    p = sub.add_parser("eventlosses", help="loss of every event of an event set (event loss table)")
    p.add_argument("--name", default="eventset")
    p.add_argument("--gmpe", choices=["ensemble", "eshm20"], default="ensemble")
    p.add_argument("--workers", type=int, default=None)

    p = sub.add_parser("riskmetrics", help="average annual loss and exceedance curves from an event loss table")
    p.add_argument("--name", default="eventset")
    p.add_argument("--gmpe", choices=["ensemble", "eshm20"], default="ensemble")

    p = sub.add_parser("serve", help="start the dashboard (default http://localhost:8000, or the port in $PORT)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))

    p = sub.add_parser("scenario", help="run one scenario and print the summary as JSON")
    p.add_argument("--lon", type=float, required=True)
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--mag", type=float, required=True)
    p.add_argument("--depth", type=float, default=10.0, help="hypocentre depth in km")
    p.add_argument("--strike", type=float, default=0.0, help="fault strike in degrees")
    p.add_argument("--dip", type=float, default=60.0)
    p.add_argument("--rake", type=float, default=-90.0, help="-90 normal, 0 strike-slip, 90 thrust")
    p.add_argument("--vs30", type=float, default=400.0, help="uniform Vs30 in m/s")
    p.add_argument("--gmpe", choices=["ensemble", "eshm20", "akkar"], default="ensemble")
    p.add_argument("--cells", help="write the per-cell results to this parquet file")

    args = parser.parse_args(argv)
    if args.command == "download":
        from .download_data import download
        download(force=args.force)
    elif args.command == "build":
        for step in STEPS:
            _run_step(step)
    elif args.command in STEPS:
        _run_step(args.command)
    elif args.command == "eventset":
        from .eventset import generate
        generate(args.years, args.samples, args.name)
    elif args.command == "eventlosses":
        from .eventloss import compute
        compute(args.name, args.gmpe, args.workers)
    elif args.command == "riskmetrics":
        from .riskmetrics import compute
        compute(args.name, args.gmpe)
    elif args.command == "serve":
        from .server import serve
        serve(args.host, args.port)
    else:
        from .engine import QuakeEngine
        result = QuakeEngine().run(args.lon, args.lat, args.mag, args.depth, args.strike, args.dip, args.rake,
                                   args.vs30, args.gmpe)
        print(json.dumps(result.summary(), indent=1, default=float))
        if args.cells:
            result.cell_table().to_parquet(args.cells, index=False)


if __name__ == "__main__":
    main()
