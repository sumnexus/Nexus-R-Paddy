"""Command line: python -m paddy <command> [options]

  serve        the REST API, the event stream and the website
  run          one season, headless, with a summary and a record of every call and decision
  compare      the same seasons under several managers, paired, with a cost estimate for the API
  campaign     many seasons (the validation campaigns), summary JSON
  check-api    show the decision API settings (never the key) and make one test call
  mock         run the stand-in decision API on its own
  fuzz         throw random replies at the guardrails and check that nothing unsafe gets through
  replay       re-run a recorded run from its call records and check it comes out identical
  export-replay  write a recorded run as the data file for the static replay website
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import __version__
from . import config as CFG


def _client(args, mock_srv=None):
    from .decisions.api_client import DecisionAPI
    if mock_srv is not None:
        return DecisionAPI(base_url=mock_srv.url, model='mock-builtin', key='not-a-real-key', timeout_s=getattr(args, 'timeout', 10) or 10,
                           backoff_base_s=0.02, max_calls_per_real_hour=10 ** 6, fallback=getattr(args, 'fallback', 'hold') or 'hold')
    kw = {}
    if getattr(args, 'fallback', None):
        kw['fallback'] = args.fallback
    return DecisionAPI.from_env(**kw)


def _mock(args):
    from .decisions.mock_api import MockConfig, MockDecisionServer
    return MockDecisionServer(MockConfig.parse(getattr(args, 'faults', '') or '', seed=getattr(args, 'mock_seed', 1) or 1, timeout_s=3.0)).start()


def _approver(kind):
    if kind in (None, 'none'):
        return None
    if kind == 'all':
        return lambda rec, eng: ('approve', '')
    if kind == 'cautious':
        # a careful farmer: no preventive sprays, no immature harvests, otherwise yes
        def f(rec, eng):
            msgs = ' '.join(rec['guardrail']['messages'])
            if rec['action'] == 'spray' and 'preventive' in msgs:
                return 'reject', 'no preventive sprays on my farm'
            if rec['action'] == 'harvest' and rec['params'].get('override_immature'):
                return 'reject', 'wait for the crop to ripen'
            return 'approve', ''
        return f
    raise SystemExit(f'unknown approver {kind!r} (none, all, cautious)')


def cmd_run(args):
    from .engine import Engine
    mock_srv = _mock(args) if args.mock else None
    try:
        client = _client(args, mock_srv) if args.manager.startswith('api') else None
        if client is not None and mock_srv is None and not client.configured:
            print('The decision API is not configured (set API_BASE_URL, API_MODEL and API_KEY in the environment or .env).')
            print('Use --mock to run against the stand-in API.')
            return 2
        if client is not None and mock_srv is None and not args.yes:
            from . import runs
            pin, pout = _prices(args)
            est = runs.cost_forecast(1, pin, pout)
            print(f'A season calls the real decision API about {est["calls"]} times (06:00 and 18:00 for ~140 days, plus triggers; at most '
                  f'{CFG.MAX_CALLS_PER_SEASON}): about {est["tokens_in"] / 1e6:.1f} million input and {est["tokens_out"] / 1e6:.2f} million output tokens, '
                  f'≈ US${est["usd"]:.2f} at US${pin:g} / US${pout:g} per million (API_PRICE_IN_PER_M / API_PRICE_OUT_PER_M). Add --yes to go ahead.')
            return 2
        out = args.out or os.path.join('runs', f'{time.strftime("%Y%m%d-%H%M%S")}-{args.manager}-{args.season}-{args.seed}')
        e = Engine(seed=args.seed, weatherYear=args.weather_year, season=args.season, manager=args.manager, mode=args.mode, client=client,
                   approver=_approver(args.approve), record_dir=out, forecastMode=args.forecast, record_frames=True)
        t0 = time.time()
        e.run()
        s = e.summary()
        if mock_srv is not None:
            s['mock'] = {'requests': mock_srv.stats.requests, 'faults': mock_srv.stats.faults}
        e.store.write_json('summary.json', s)
        e.store.write_json('config.json', {'cfg': e.cfg, 'api': client.describe() if client is not None else None, 'version': __version__})
        print(json.dumps({k: s[k] for k in ('run_id', 'season_days', 'yield_t_ha', 'irrigation_mm', 'n_kg_ha', 'ai_g_ha', 'ch4_kg_ha', 'calls',
                                            'calls_failed', 'fallbacks', 'tokens_in', 'tokens_out', 'decision_status', 'verdicts')}, indent=1))
        print(f'records in {out} ({time.time() - t0:.1f} s)')
        return 0
    finally:
        if mock_srv is not None:
            mock_srv.stop()


def _prices(args):
    pin = args.price_in if getattr(args, 'price_in', None) is not None else float(os.environ.get('API_PRICE_IN_PER_M') or 3.0)
    pout = args.price_out if getattr(args, 'price_out', None) is not None else float(os.environ.get('API_PRICE_OUT_PER_M') or 15.0)
    return pin, pout


def cmd_compare(args):
    from . import runs
    managers = [m.strip() for m in args.managers.split(',') if m.strip()]
    uses_api = any(m.startswith('api') for m in managers)
    mock_srv = None
    extra = {}
    if uses_api:
        # the cost estimate comes first, before anything is simulated or sent
        pin, pout = _prices(args)
        n_api = sum(1 for m in managers if m.startswith('api'))
        est = runs.cost_forecast(args.seasons * n_api, pin, pout)
        print(('Cost estimate if this used the real decision API' if args.mock else 'Cost estimate') +
              f': {args.seasons} season(s) × {n_api} API decision maker(s) × about {est["calls_per_season"]} calls = {est["calls"]:,} calls; '
              f'about {est["tokens_in_per_call"]:,} input tokens per call (instructions ≈ {est["prompt_tokens"]:,} + state ≈ {est["state_tokens"]:,}) '
              f'= {est["tokens_in"] / 1e6:.1f} million, and about {est["tokens_out_per_call"]} output tokens per call (assumed) = '
              f'{est["tokens_out"] / 1e6:.2f} million. At US${pin:g} / US${pout:g} per million input / output tokens '
              f'(API_PRICE_IN_PER_M / API_PRICE_OUT_PER_M) ≈ US${est["usd"]:,.2f}.', flush=True)
        if args.mock:
            print('Running against the stand-in mock: nothing is spent.\n', flush=True)
            mock_srv = _mock(args)
            extra['client_kw'] = dict(base_url=mock_srv.url, model='mock-builtin', key='not-a-real-key', timeout_s=10, backoff_base_s=0.02,
                                      max_calls_per_real_hour=10 ** 6)
        else:
            from .decisions.api_client import DecisionAPI
            c = DecisionAPI.from_env()
            if not c.configured:
                print('The decision API is not configured; use --mock for the stand-in.')
                return 2
            if not args.yes:
                print('Add --yes to spend that.')
                return 2
            extra['client_kw'] = dict(base_url=c.base_url, model=c.model, key=os.environ.get('API_KEY'), adapter=c.adapter_name, timeout_s=c.timeout_s,
                                      temperature=c.temperature, json_mode=c.json_mode, retries=c.retries, fallback=c.fallback)
    try:
        cs = runs.cases(args.seasons, base_seed=args.base_seed, first_year=args.first_year)
        workers = 1 if (uses_api and not args.mock) else args.workers
        res = runs.run_cases(cs, managers, workers=workers, extra=extra,
                             progress=lambda i, n, r: print(f'  {i}/{n} {r["manager"]:<14} {r["case"]["season"]:<6} seed {r["case"]["seed"]}: '
                                                            f'{r["yield_t_ha"]:.2f} t/ha, {r["irrigation_mm"]:.0f} mm', flush=True))
        S = runs.summarise(res)
        print()
        print(runs.table(S, managers))
        out = {'cases': cs, 'managers': managers, 'summary': S, 'paired': {}}
        base = managers[-1]
        for m in managers[:-1]:
            for k in ('yield_t_ha', 'irrigation_mm', 'n_kg_ha', 'ai_g_ha', 'ch4_kg_ha'):
                out['paired'][f'{m}-{base}:{k}'] = runs.paired(res, m, base, k)
        for m in managers:
            if m.startswith('api'):
                pin, pout = _prices(args)
                ce = runs.cost_estimate(S[m], pin, pout)
                out.setdefault('cost', {})[m] = ce
                print(f'\n{m}: {ce["tokens_in_per_season"]:,.0f} input + {ce["tokens_out_per_season"]:,.0f} output tokens per season '
                      f'≈ US${ce["usd_per_season"]:.2f} per season at ${pin:g}/${pout:g} per million tokens'
                      + (' (token counts from the stand-in: the real model\'s replies will be longer)' if args.mock else ''))
        if mock_srv is not None:
            digests = {}
            for r in res:
                digests.setdefault(json.dumps(r['case'], sort_keys=True), {})[r['manager']] = r['digest']
            same = sum(1 for d in digests.values() if 'api' in d and 'builtin' in d and d['api'] == d['builtin'])
            tot = sum(1 for d in digests.values() if 'api' in d and 'builtin' in d)
            if tot:
                print(f'\nstand-in check: api(mock) made exactly the built-in decisions in {same} of {tot} seasons')
                out['mock_identical'] = {'same': same, 'of': tot}
        if args.out:
            with open(args.out, 'w', encoding='utf-8') as fh:
                json.dump({**out, 'results': res}, fh, default=str)
            print(f'\nwritten to {args.out}')
        return 0
    finally:
        if mock_srv is not None:
            mock_srv.stop()


def cmd_campaign(args):
    from . import runs
    managers = [m.strip() for m in args.managers.split(',') if m.strip()]
    cs = runs.cases(args.seasons, base_seed=args.base_seed, first_year=args.first_year)
    t0 = time.time()
    res = runs.run_cases(cs, managers, workers=args.workers, extra={'keep_history': args.history},
                         progress=lambda i, n, r: print(f'{i}/{n} {r["manager"]} {r["case"]["season"]} {r["yield_t_ha"]:.2f} t/ha {r["wall_total_s"]:.1f}s', flush=True)
                         if i % 20 == 0 or i == n else None)
    S = runs.summarise(res)
    print(runs.table(S, managers))
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as fh:
            json.dump({'cases': cs, 'managers': managers, 'summary': S, 'results': res, 'elapsed_s': time.time() - t0, 'version': __version__}, fh, default=str)
        print(f'written to {args.out}')
    return 0


def cmd_check_api(args):
    from .decisions.api_client import DecisionAPI
    from .decisions.state import build_state
    from .engine import Engine
    mock_srv = _mock(args) if args.mock else None
    try:
        c = _client(args, mock_srv)
        d = c.describe()
        print('Decision API settings (the key itself is never shown):')
        for k, v in d.items():
            print(f'  {k:<24} {v}')
        if not c.configured:
            print('\nNot configured: set API_BASE_URL and API_MODEL (and API_KEY) in the environment or in a .env file next to README.md.')
            return 2
        if args.dry_run:
            print('\n--dry-run: no call made.')
            return 0
        e = Engine(seed=1, manager='api', client=c, keep_states=False)
        st = build_state(e, ['check-api'])
        print(f'\nSending one test call ({len(json.dumps(st)):,} characters of state)…')
        r = c.decide(st)
        print(f'  ok: {r.ok}   source: {r.source}   model: {r.model}   latency: {r.latency_ms:.0f} ms   attempts: {r.attempts}   repaired: {r.repaired}')
        print(f'  tokens: {r.tokens_in} in, {r.tokens_out} out' + (f'   cost ≈ US${c.cost_usd(r.tokens_in, r.tokens_out):.4f}' if c.cost_usd(r.tokens_in, r.tokens_out) is not None else ''))
        if r.error:
            print(f'  error: {r.error}')
        if r.ok:
            from .decisions.schema import validate
            ds, errs = validate(r.response)
            print(f'  {len(ds)} valid decision(s), {len(errs)} schema problem(s)')
            for x in ds[:6]:
                print(f'    - {x["field"]} {x["action"]} {x["params"]}: {x["reason"][:100]}')
            for x in errs[:6]:
                print(f'    ! {x}')
        return 0 if r.ok else 1
    finally:
        if mock_srv is not None:
            mock_srv.stop()


def cmd_mock(args):
    from .decisions.mock_api import MockConfig, MockDecisionServer
    srv = MockDecisionServer(MockConfig.parse(args.faults or '', seed=args.mock_seed, timeout_s=args.timeout_s), host=args.host, port=args.port)
    print(f'Stand-in decision API on {srv.url}  (chat completions at {srv.url}/chat/completions; faults: {args.faults or "none"})')
    print('Point the farm at it with API_BASE_URL=' + srv.url + ' API_MODEL=mock-builtin')
    try:
        srv.httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_fuzz(args):
    from .fuzz import fuzz, fuzz_parallel
    rep = fuzz(n=args.n, seed=args.seed, verbose=args.verbose) if args.workers <= 1 else fuzz_parallel(args.n, args.seed, args.workers, args.verbose)
    print(json.dumps(rep, indent=1))
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as fh:
            json.dump(rep, fh, indent=1, default=str)
        print(f'written to {args.out}')
    return 0 if rep['violations'] == 0 and rep['crashes'] == 0 else 1


def _load_calls(run_dir):
    calls = []
    with open(os.path.join(run_dir, 'calls.jsonl'), encoding='utf-8') as fh:
        for line in fh:
            calls.append(json.loads(line))
    return calls


def cmd_replay(args):
    from .decisions.api_client import ReplayClient
    from .engine import Engine
    with open(os.path.join(args.run_dir, 'config.json'), encoding='utf-8') as fh:
        cfg = json.load(fh)['cfg']
    with open(os.path.join(args.run_dir, 'summary.json'), encoding='utf-8') as fh:
        summ = json.load(fh)
    calls = _load_calls(args.run_dir)
    client = ReplayClient(calls, fallback=next((c['fallback'] for c in calls if c.get('fallback')), 'hold'))
    mgr = cfg['manager']
    e = Engine(seed=cfg['seed'], weatherYear=cfg['weatherYear'], season=cfg['season'], manager=mgr if mgr.startswith('api') else mgr,
               mode='auto', client=client if mgr.startswith('api') else None, models=cfg.get('models', 'v1'),
               builtin_water=cfg.get('builtin_water', 'awd'), events=cfg.get('events'), arms=cfg.get('arms', True),
               forecastMode=None if cfg.get('forecastMode') in (None, 'default') else cfg.get('forecastMode'))
    e.run()
    s = e.summary()
    keys = ('yield_t_ha', 'irrigation_mm', 'n_kg_ha', 'ai_g_ha', 'ch4_kg_ha', 'decisions', 'decision_status', 'decision_digest', 'history_digest')
    keys = tuple(k for k in keys if k in summ)
    same = all(s[k] == summ[k] for k in keys)
    for k in keys:
        print(f'  {k:<16} recorded {summ[k]}   replayed {s[k]}')
    print('identical' if same else 'DIFFERENT')
    return 0 if same else 1


def cmd_export_replay(args):
    from .replay import export_replay
    camps = []
    for spec in args.campaign or []:
        path, _, rest = spec.partition('=')
        title, _, note = rest.partition('|')
        camps.append((path, title or None, None, note or args.campaign_note))
    out = export_replay(args.run_dir, args.out, baseline=args.baseline, campaigns=camps)
    print(f'written {out}')
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog='python -m paddy', description='Paddy API Farm: two 6 × 6 paddy fields run by an external decision API.')
    p.add_argument('--version', action='version', version=__version__)
    sub = p.add_subparsers(dest='cmd', required=True)

    def season_opts(sp):
        sp.add_argument('--seed', type=int, default=20261024)
        sp.add_argument('--season', choices=list(CFG.SEASONS), default='kharif')
        sp.add_argument('--weather-year', type=int, default=0, dest='weather_year')

    sp = sub.add_parser('serve', help='REST API + website')
    sp.add_argument('--host', default='127.0.0.1')
    sp.add_argument('--port', type=int, default=8000)
    sp.add_argument('--manager', default='builtin', choices=['builtin', 'conventional', 'api', 'api-no-advice'])
    sp.add_argument('--mode', default='auto', choices=['auto', 'approve'])
    sp.add_argument('--mock', action='store_true', help='decisions for "api" come from the stand-in API')
    sp.add_argument('--faults', default='', help='stand-in fault injection, e.g. p429=0.05,pmalformed=0.05')
    sp.add_argument('--static', default=None, help='folder with the built website (default frontend/dist)')
    sp.add_argument('--runs-dir', default='runs')
    sp.add_argument('--speed', type=float, default=24.0, help='simulated hours per real second')
    sp.add_argument('--cors', default='*', help='allowed origins for the website, comma separated, or *')
    sp.add_argument('--verbose', action='store_true')
    season_opts(sp)

    sp = sub.add_parser('run', help='one season, headless')
    season_opts(sp)
    sp.add_argument('--manager', default='builtin', choices=['builtin', 'conventional', 'api', 'api-no-advice'])
    sp.add_argument('--mode', default='auto', choices=['auto', 'approve'])
    sp.add_argument('--approve', default=None, help='scripted approvals in approve mode: all | cautious')
    sp.add_argument('--mock', action='store_true')
    sp.add_argument('--faults', default='')
    sp.add_argument('--fallback', choices=['hold', 'builtin'], default=None)
    sp.add_argument('--forecast', choices=['default', 'realistic'], default=None)
    sp.add_argument('--out', default=None)
    sp.add_argument('--yes', action='store_true', help='allow calls to the real (paid) API')

    sp = sub.add_parser('compare', help='several managers on the same seasons')
    sp.add_argument('--managers', default='api,builtin,conventional')
    sp.add_argument('--seasons', type=int, default=10)
    sp.add_argument('--base-seed', type=int, default=5100, dest='base_seed')
    sp.add_argument('--first-year', type=int, default=101, dest='first_year')
    sp.add_argument('--mock', action='store_true')
    sp.add_argument('--faults', default='')
    sp.add_argument('--workers', type=int, default=None)
    sp.add_argument('--price-in', type=float, default=None, dest='price_in', help='US$ per million input tokens')
    sp.add_argument('--price-out', type=float, default=None, dest='price_out', help='US$ per million output tokens')
    sp.add_argument('--out', default=None)
    sp.add_argument('--yes', action='store_true')

    sp = sub.add_parser('campaign', help='many seasons, summary JSON')
    sp.add_argument('--managers', default='builtin,conventional')
    sp.add_argument('--seasons', type=int, default=200)
    sp.add_argument('--base-seed', type=int, default=7000, dest='base_seed')
    sp.add_argument('--first-year', type=int, default=1, dest='first_year')
    sp.add_argument('--workers', type=int, default=None)
    sp.add_argument('--history', action='store_true')
    sp.add_argument('--out', default=None)

    sp = sub.add_parser('check-api', help='settings and one test call')
    sp.add_argument('--mock', action='store_true')
    sp.add_argument('--faults', default='')
    sp.add_argument('--dry-run', action='store_true', dest='dry_run')

    sp = sub.add_parser('mock', help='run the stand-in decision API')
    sp.add_argument('--host', default='127.0.0.1')
    sp.add_argument('--port', type=int, default=8765)
    sp.add_argument('--faults', default='')
    sp.add_argument('--mock-seed', type=int, default=1, dest='mock_seed')
    sp.add_argument('--timeout-s', type=float, default=3.0, dest='timeout_s')

    sp = sub.add_parser('fuzz', help='guardrail fuzzing')
    sp.add_argument('--n', type=int, default=10000)
    sp.add_argument('--seed', type=int, default=1)
    sp.add_argument('--workers', type=int, default=1, help='parallel processes (each with its own seed and share of the replies)')
    sp.add_argument('--out', default=None, help='write the report as JSON')
    sp.add_argument('--verbose', action='store_true')

    sp = sub.add_parser('replay', help='re-run a recorded run and compare')
    sp.add_argument('run_dir')

    sp = sub.add_parser('export-replay', help='recorded run → website replay data')
    sp.add_argument('run_dir')
    sp.add_argument('--out', default=None)
    sp.add_argument('--baseline', action='store_true', help='also run conventional practice on the same weather, for the charts')
    sp.add_argument('--campaign', action='append', help='campaign/compare JSON to show in the Compare view: path, path=Title or path=Title|Note (repeatable)')
    sp.add_argument('--campaign-note', default=None)

    sp = sub.add_parser('batch', help='v2 data sets: run seasons of a set, resumable (docs/DEFINITIONS-paddy-v2.md)')
    sp.add_argument('--set', required=True, choices=['G', 'T', 'L', 'H', 'W'], dest='set_name')
    sp.add_argument('--start', type=int, default=0)
    sp.add_argument('--stop', type=int, required=True)
    sp.add_argument('--workers', type=int, default=2)
    sp.add_argument('--minutes', type=float, default=100.0, help='stop taking new seasons after this long')
    sp.add_argument('--out', default=None, help='default runs/datasets')
    sp.add_argument('--managers', default=None, help='comma-separated subset of builtin,conventional,api-no-advice,builtin-cf')
    sp.add_argument('--no-rows', action='store_true', dest='no_rows')
    sp.add_argument('--models', default=None, help='a model pack to use instead of the shipped forests')
    sp.add_argument('--tag', default=None, help='sub-folder for this run (e.g. a candidate name)')
    sp.add_argument('--forecast', choices=['default', 'realistic'], default=None,
                    help='the harder forecast mode (off by default; reported with and without)')

    sp = sub.add_parser('sets', help='v2 data sets: the overlap check (seeds and weather years)')

    args = p.parse_args(argv)
    if args.cmd == 'batch':
        from . import datasets as DS
        out = args.out or DS.DEFAULT_OUT
        if args.tag:
            out = os.path.join(out, args.tag)
        os.makedirs(out, exist_ok=True)
        logf = open(os.path.join(out, f'{args.set_name}.log'), 'a', encoding='utf-8')

        def log(msg):
            line = f'{time.strftime("%Y-%m-%d %H:%M:%S")} {msg}'
            print(line, flush=True)
            logf.write(line + '\n')
            logf.flush()
        mgrs = tuple(args.managers.split(',')) if args.managers else None
        done, err = DS.run_batch(args.set_name, args.start, args.stop, workers=args.workers, minutes=args.minutes, out=out,
                                 rows=not args.no_rows, managers=mgrs, models_path=args.models, log=log,
                                 extra={'forecastMode': 'realistic'} if args.forecast == 'realistic' else None)
        return 0 if not err else 1
    if args.cmd == 'sets':
        from . import datasets as DS
        r = DS.overlap_report()
        for k, v in r['spans'].items():
            print(f'{k:20s} seeds {v["seeds"][0]:>10,}–{v["seeds"][1]:>10,}   weather years {v["weather_years"][0]:>7,}–{v["weather_years"][1]:>7,}')
        bad = [x for x in r['pairs'] if x['shared_seeds'] or x['shared_years']]
        print(f'{len(r["pairs"])} pairs checked; shared seeds or weather years: {r["total_shared"]}')
        for x in bad:
            print('  OVERLAP', x)
        return 0 if not bad else 1
    if args.cmd == 'serve':
        from .server import serve
        serve(host=args.host, port=args.port, manager=args.manager, mode=args.mode, seed=args.seed, season=args.season,
              weatherYear=args.weather_year, mock=args.mock, mock_faults=args.faults, static=args.static, runs_dir=args.runs_dir,
              verbose=args.verbose, cors='*' if args.cors == '*' else [x.strip() for x in args.cors.split(',')], speed=args.speed)
        return 0
    return {'run': cmd_run, 'compare': cmd_compare, 'campaign': cmd_campaign, 'check-api': cmd_check_api, 'mock': cmd_mock,
            'fuzz': cmd_fuzz, 'replay': cmd_replay, 'export-replay': cmd_export_replay}[args.cmd](args)


if __name__ == '__main__':
    sys.exit(main())
