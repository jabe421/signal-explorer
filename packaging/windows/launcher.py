"""Shared frozen entry point for the windowed GUI and console/pipe worker."""
import multiprocessing
import sys


if __name__ == '__main__':
    multiprocessing.freeze_support()
    if '--script-worker' in sys.argv:
        from signal_explorer.scripting.worker import main
        main()
    elif '--packaging-smoke-test' in sys.argv:
        from signal_explorer.packaging_smoke import main
        raise SystemExit(main(sys.argv[sys.argv.index('--packaging-smoke-test') + 1]))
    else:
        from signal_explorer.app import main
        raise SystemExit(main())
