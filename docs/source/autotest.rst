Autotest Framework
==================

Overview
--------

The bundled pytest plugin can discover and run API client methods marked with
``@autotest`` and validate their JSON payloads through ``schemashot`` from
``pytest-jsonschema-snapshot``.

Design goals:

* no manual test per endpoint method;
* no pytest import in business layer;
* hooks/params/dependencies are declared only in test layer.


Installation
------------

.. code-block:: bash

    pip install pytest pytest-anyio pytest-jsonschema-snapshot pytest-subtests human-requests[autotest]


Pytest Configuration
--------------------

Enable anyio mode and provide the root API class in ``pytest.ini``:

.. code-block:: ini

    [pytest]
    anyio_mode = auto
    autotest_start_class = your_package.StartClass
    autotest_typecheck = off

``autotest_start_class`` must be a dotted class path (``module.ClassName``).

``autotest_typecheck`` controls runtime validation of ``@autotest_params`` output
against method argument annotations:

* ``off`` (default): do not check annotation compatibility;
* ``warn``: emit ``RuntimeWarning`` on mismatch;
* ``strict``: raise ``TypeError`` on mismatch.

If an annotation cannot be resolved at runtime (for example unresolved forward
reference), that parameter is skipped by the type checker.


Required Fixtures
-----------------

The plugin expects two fixtures:

* ``api``: instance of ``autotest_start_class`` (sync or async fixture);
* ``schemashot``: object with ``assert_json_match(data, name)`` method.

Example:

.. code-block:: python

    import pytest
    from your_package import StartClass

    @pytest.fixture(scope="session")
    def anyio_backend() -> str:
        return "asyncio"

    @pytest.fixture(scope="session")
    async def api() -> StartClass:
        async with StartClass() as client:
            yield client

The plugin adds one runtime test item: ``test_autotest_api_methods``.
When ``pytest-subtests`` is installed, each discovered ``@autotest`` method
and each ``@autotest_data`` case is reported as a separate subtest entry.


Business Layer Marker
---------------------

Only mark target methods:

.. code-block:: python

    from human_requests import autotest

    class Catalog:
        @autotest
        async def tree(self):
            ...

Internally this sets ``func.__autotest__ = True``.


Test Layer Registration
-----------------------

Register custom behavior in test modules (usually ``tests/endpoints/*``).

Hook
~~~~

Use ``@autotest_hook`` to transform/assert payload before snapshot:

.. code-block:: python

    from human_requests import autotest_hook
    from human_requests.autotest import AutotestContext

    @autotest_hook(target=Catalog.tree)
    def _capture(_resp, data, ctx: AutotestContext):
        ctx.state["category_id"] = data["items"][0]["id"]

``parent=...`` can scope a hook to immediate parent class:

.. code-block:: python

    @autotest_hook(target=Child.method, parent=ParentA)
    def _only_for_parent_a(_resp, data, ctx):
        ...

Match priority is:

1. ``(parent_class, func)``
2. ``(None, func)``

Params Provider
~~~~~~~~~~~~~~~

Use ``@autotest_params`` when method requires arguments:

.. code-block:: python

    from human_requests import autotest_params
    from human_requests.autotest import AutotestCallContext

    @autotest_params(target=Catalog.feed)
    def _feed_params(ctx: AutotestCallContext):
        return {"category_id": ctx.state["category_id"]}

Provider return values:

* ``dict`` -> keyword arguments;
* ``tuple``/``list`` -> positional arguments;
* ``AutotestInvocation`` -> explicit ``args`` + ``kwargs``;
* ``None`` -> no args.

Extra Snapshot Data
~~~~~~~~~~~~~~~~~~~

Use ``@autotest_data`` for additional payloads outside endpoint methods:

.. code-block:: python

    from human_requests import autotest_data

    @autotest_data(name="unstandard_headers")
    def _headers(ctx):
        return ctx.api.unstandard_headers


Dependencies
------------

Two options are available:

* ``@autotest_policy(target=..., depends_on=[...])``
* ``@autotest_depends_on(...)`` marker on hook/params callbacks

``@autotest_depends_on`` can be stacked multiple times:

.. code-block:: python

    from human_requests import autotest_depends_on, autotest_params

    @autotest_depends_on(Api.prepare_city)
    @autotest_depends_on(Api.prepare_shop)
    @autotest_params(target=Api.dependent_method)
    def _params(ctx):
        ...

If any dependency was skipped/not executed, dependent case is not run.


Runtime Context Objects
-----------------------

``AutotestContext`` (hook):

* ``api``: root API object;
* ``owner``: object that owns tested method;
* ``parent``: immediate parent object;
* ``method``: bound async method;
* ``func``: original function object;
* ``schemashot``: snapshot fixture;
* ``state``: shared mutable dict for cross-case data.

``AutotestCallContext`` (params provider) has the same fields.

``AutotestDataContext`` (data provider):

* ``api``, ``schemashot``, ``state``.


Execution Flow
--------------

For each discovered ``@autotest`` method:

1. resolve method args via ``autotest_params`` (if registered);
2. call async method and parse ``response.json()``;
3. run matching hook (if any);
4. pass final payload into ``schemashot.assert_json_match(data, func)``.

After method cases, all ``@autotest_data`` providers are executed.


Anyio Note
----------

For async API fixtures/methods, use ``pytest-anyio`` (``anyio_mode = auto``).
The plugin detects anyio automatically and runs in its runner context.
