To use these testable documents, invoke
> pytest metacognition/metacognition_trials/

Notes on use of "contextlib"

Doctests work by looking at the line immediately following a >>> command. If there is no text there, doctest expects that absolutely nothing will be printed to the console during the execution of that command.
Because our run_blueprint function and the underlying AI/RAG services are intentionally heavily instrumented with print() statements (e.g., Initializing RAG Service..., 🪝 Received Execution Plan...), doctest sees all this terminal output, compares it to the "Expected nothing" implicitly defined in your .rst file, and flags it as a failure!

We don't want to remove our print statements because they are incredibly useful for debugging and tracking the agent's thought process in the server logs.
Instead, we can simply wrap the run_blueprint call in our .rst tutorials with Python's built-in contextlib.redirect_stdout. This intercepts all the print statements and throws them into a temporary memory buffer so doctest doesn't see them, allowing the test to smoothly proceed to the assertion!