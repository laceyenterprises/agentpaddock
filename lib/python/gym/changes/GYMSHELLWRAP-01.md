Tool-call command normalization unwraps Python invocations of
`shell_output_limit.py`, including an optional `exec` prefix. The `--bytes`,
`--steer-policy`, `--shell`, and `--command` options accept separate values or
`--option=value` form. The command body is normalized recursively, so verifier
checks can recognize commands inside nested shell and boundary-steer wrappers
while retaining the original tool-call arguments.

Missing option values, unknown options, and unsupported Python interpreter
modes or flags retain the invocation verbatim. In particular, `-m` and `-c`
are not interpreted as script invocations.
