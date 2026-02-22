.. _browser-antibot-report:

Browser Anti-Bot Report
=======================

Raw dataset for this report is stored in project CI artifacts and is not bundled
into the source tree by default.

This page intentionally keeps a lightweight default rendering pipeline.
If you need matrix/plot visualization, use the project scripts and CI artifacts.

Currently, stealth mode erases all "automatic places",
so it makes sense to use camoufox only if you have massive traffic
*(or you were banned by a fingerprint while writing a library >:) )*.

The problem with `Broken Image Dimensions` is caused not by browsers, but by
``HumanPage.goto_render`` particularities; there are no errors with direct
``goto`` navigation *(and it's not critical anyway)*.

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
