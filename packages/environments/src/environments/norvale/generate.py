"""Run the native v2 generator; use --help for options."""
import sys

def main(argv=None, *, transport_factory=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ['feedback']:
        from environments.norvale.integrations.quality_feedback.__main__ import main as feedback
        return feedback(argv[1:])
    if argv[:1] == ['incremental']:
        from environments.norvale.native.incremental import main as incremental_main
        return incremental_main(argv[1:], transport_factory=transport_factory)
    from environments.norvale.native.company import main as company_main
    return company_main(argv)


if __name__ == '__main__':
    main()
