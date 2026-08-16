"""Nimbus supervisor — the conversational layer over the other agents.

Three modules, deliberately separated:

  * ``client``  — the Anthropic call, the spend ledger, the cap. Knows nothing
    about marketing.
  * ``tools``   — what the supervisor may look at and what it may set running.
    Knows nothing about Claude.
  * ``chat``    — the loop that joins them and persists the conversation.

The split is what makes the powers auditable: ``tools.TOOLS`` is the complete
list of everything the supervisor can do, in one file, and
``test_supervisor_cannot_approve`` reads it.
"""
