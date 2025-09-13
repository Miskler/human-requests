.. _browser_selection:
Browser Selection Recommendations
=================================

The best among fast browsers is **WebKit**. It bypasses about 90% of checks without a stealth patch, while remaining extremely fast and stable.

.. note::

    Useful if you are not planning to make large-scale requests. It will pass most antibot checks and provide high speed.

The best among stealth browsers is **Camoufox**. It is very slow, with initialization taking about 2.3 times longer.
The advantage, however, is that it passes most checks and also has built-in spoofing
(which means the site will not recognize that requests come from the same browser — the fingerprint will be different).

.. note::

    Useful if you are going to make large-scale requests. The browser is only used to warm up the session, so a few extra seconds can be tolerated.

To learn on what basis these conclusions were made, see :ref:`browser-antibot-report`

.. toctree::
   :maxdepth: 4

   sannysoft_antibot
