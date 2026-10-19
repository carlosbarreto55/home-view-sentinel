"""Running the components on the Mac: containers, launchd agents and logs.

Every component runs under a launchd agent with ``KeepAlive`` set to restart it
only after a failed exit. So a component exits with ``EXIT_RESTART`` when
launchd should start it again (a crash, a lost container, the DVR not found),
and with ``EXIT_DONE`` when it must stay stopped until the user acts (stopped on
purpose, invalid configuration, missing secrets, a rejected DVR login).
"""

EXIT_DONE = 0
EXIT_RESTART = 1
