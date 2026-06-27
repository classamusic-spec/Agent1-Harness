"""Agent1-Harness: an agent that builds applications from a spec.

The harness wraps the Claude Agent SDK in a deterministic control loop:

    spec -> plan -> implement -> VERIFY -> (repair -> VERIFY)* -> done

The verification gate (see `harness.verifier`) is the core robustness lever:
the agent does not get to declare success. Build / test / lint must pass.
"""

__version__ = "0.1.0"
