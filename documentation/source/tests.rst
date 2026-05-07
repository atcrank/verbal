Testing
========


Philosophy
----------

The primary test philosophy for this app during rapid development and prototyping is end-to-end testing: if a test case
in background_resources fails due to a problem in the llm_api components, that result is real an important.

At the same time, as the app grows and function complexity increases, the test time might become excessive, so
'mocking' of major services may be worthwhile. The metacognition app defines both mocked tests, which will test only
progress of a Blueprint through its Steps, including step failure and repetition.

Running tests
-------------

To run quick tests, mocking the most time-consuming processing (mainly the AI services) run:

>python manage.py test --exclude-tag=e2e

To test only end-to-end:

make sure the inference server and worker server are running locally in separate terminals:
> . start_inference.sh
> . toggle_background_task_service.sh (ensuring this leaves the worker instance and the redis container "on")

Then test in a third terminal:
> python manage.py test --tag=e2e
