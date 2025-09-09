Sannysoft Report
================

Legend:

- ✅ — test never failed.
- ❌ base[, unstable] / ❌ stealth[, unstable] — failed only in the specified mode (yellow background).
- ❌[, unstable] — failed everywhere (red background).

.. antibot-table:: ../../tests/sannysoft/browser_antibot_sannysoft.json
   :title: Sannysoft Anti-bot Matrix

Currently, stealth mode erases all "automatic places",
so it makes sense to use camoufox only if you have massive traffic
*(or you were banned by a fingerprint while writing a library >:) )*.

The problem with `Broken Image Dimensions` is caused not by browsers, but by Response.render
particularities; there are no errors with goto requests *(and it's not critical anyway)*


.. antibot-speed-plot:: ../../tests/sannysoft/browser_antibot_sannysoft.json
   :title: Browser Speed (avg ± min/max)
   :outfile: _static/generated/antibot_speed

Practical Recommendations
-------------------------
Below are general tips that, on average, reduce the "bot footprint" and
stabilize the output:

1) Run the browser **headed under Xvfb** in CI  
   This reduces the characteristic traces of headless mode (WebGL renderers
   like llvmpipe/SwiftShader, HEADCHR_* and others).

   .. code-block:: bash

      xvfb-run -s "-screen 0 1920x1080x24" your-command

2) Consider "unstable" checks  
   The *unstable* labels mean that the signal fires irregularly (depending on
   the environment, network conditions, and load). Try to rely on
   **stable** differences between modes.

Disclaimer
----------
- The set of codecs, GPU/drivers, system libraries, and security policies
  of the execution environment may affect the results (especially in headless
  environments).
- The table and graph are built on the basis of the latest published report;
  values change as browsers and environments are updated.

