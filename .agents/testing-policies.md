---
name: testing-policies
description: Create tests appropriate for this project
---

1. This project uses 'end-to-end first' testing philosophy for the core tests of apps.
2. Most testing uses Django TestCases. pytest is available if the Django TestCase doesn't support tests well.
3. Within a test in tests.py, some 'mocking' of services is permissible for speed, but considering all of tests.py, all functionality should be exercised end-to-end.
4. A pytest 'doctest' pattern should be used for rich features where an end-to-end pattern also provides a usage demo.
