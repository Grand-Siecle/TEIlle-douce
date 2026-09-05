"""What a run did, what it lost, and how that is written down.

Kept apart from `cli/` on purpose: nothing in here touches a console, a
clock or a file. The rendering is a pure function of (state, width,
capabilities), which is what makes the panel testable without a terminal.
"""
