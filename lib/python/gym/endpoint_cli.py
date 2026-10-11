"""hq gym endpoint command interface."""

import argparse
import json
import sys

from gym.endpoints import Manager, duration
from gym.endpoint_api import EndpointError


def main(argv=None):
    parser = argparse.ArgumentParser(description="Gym dedicated endpoint lifecycle")
    sub = parser.add_subparsers(dest="command", required=True)
    up = sub.add_parser("up")
    up.add_argument("--provider", choices=["fireworks"], required=True)
    up.add_argument("--base-model", required=True)
    up.add_argument("--account", required=True)
    up.add_argument("--fireworks-ref", required=True)
    up.add_argument("--litellm-ref", required=True)
    up.add_argument("--litellm-url", default="http://127.0.0.1:4000")
    up.add_argument("--cap-usd", type=float, required=True)
    up.add_argument("--ttl", type=duration, required=True)
    up.add_argument("--shape", default="auto-min")
    up.add_argument("--creation-timeout", type=duration, default=600)
    up.add_argument("--capacity-timeout", type=duration, default=180)
    up.add_argument("--route-timeout", type=duration, default=600)
    up.add_argument(
        "--cell-timeout",
        type=duration,
        default=3600,
        help="minimum cell budget reserved before endpoint creation",
    )
    sub.add_parser("status")
    sub.add_parser("reap")
    down = sub.add_parser("down")
    down.add_argument("lease")
    costs = sub.add_parser("cost-results")
    costs.add_argument("lease")
    costs.add_argument("--results", required=True)
    args = parser.parse_args(argv)
    manager = Manager()
    try:
        if args.command == "up":
            # Fail before persisting caller-supplied secret material.
            if not all(ref.startswith("op://") for ref in (args.fireworks_ref, args.litellm_ref)):
                raise EndpointError("credentials must be op:// references")
            config = {
                "account": args.account,
                "fireworks_ref": args.fireworks_ref,
                "litellm_ref": args.litellm_ref,
                "litellm_url": args.litellm_url,
            }
            result = manager.up(
                config,
                args.base_model,
                args.cap_usd,
                args.ttl,
                args.shape,
                args.creation_timeout,
                args.capacity_timeout,
                args.route_timeout,
                args.cell_timeout,
            )
        elif args.command == "down":
            result = manager.down(args.lease)
        elif args.command == "cost-results":
            from gym.endpoint_results import rejoin

            result = rejoin(manager, args.lease, args.results)
        elif args.command == "reap":
            result = manager.reap()
        else:
            result = manager.status()
        print(json.dumps(result, indent=2))
        return 0
    except EndpointError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        print(
            "endpoint operation failed; inspect endpoint audit and retry cleanup", file=sys.stderr
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
