"""The server's guide page: how to read LMU's own telemetry recordings.

People search for this ("LMU telemetry files", "Le Mans Ultimate duckdb"), so
it's written for them, from what ``lmu_core.recording`` and ``lmu_core.laps``
found in real recordings.
"""

from __future__ import annotations

PATH = "/guide/telemetry-files"
TITLE = "How to read Le Mans Ultimate telemetry files"
DESCRIPTION = ("Where Le Mans Ultimate saves its telemetry, what's in the DuckDB "
               "files, and how to read them with Python or LMU Lap Analyser.")

BODY = """
<p>Le Mans Ultimate (LMU) can record telemetry itself, with no plugin: speed,
pedals, steering, gear, RPM, tyre temperatures and pressures, the car's
position and more, for every lap you drive. This page explains where the files
are, what's in them and how to read them.</p>

<h2>Turning recording on</h2>
<p>In LMU, go to <b>Settings → Gameplay</b> and turn on telemetry recording.
Each session you drive is then saved as one file.</p>

<h2>Where the files are</h2>
<p>In the game's folder, under <code>UserData\\Telemetry</code>. With a default
Steam install:</p>
<pre>C:\\Program Files (x86)\\Steam\\steamapps\\common\\Le Mans Ultimate\\UserData\\Telemetry</pre>
<p>Each file is named after the track, the session (P for practice, Q for
qualifying, R for race) and when it started, for example
<code>Algarve International Circuit_P_2026-10-30T12_56_57Z.duckdb</code>.</p>

<h2>What's in a file</h2>
<p>Each file is a <a href="https://duckdb.org/">DuckDB</a> database: one table
per channel, plus three tables that describe them.</p>
<ul>
<li><code>metadata</code> (key, value): the driver, track and layout, car and
class, session type, weather, and the car setup as JSON under
<code>CarSetup</code>. It also holds your Steam ID and in-game name, so think
before sharing a whole file.</li>
<li><code>channelsList</code> (channelName, frequency, unit): about 65
continuous channels, from 1 to 100 samples a second. Their tables have no time
column: sample <i>i</i> is at the first <code>GPS Time</code> value plus
<i>i</i> / frequency.</li>
<li><code>eventsList</code> (eventName, unit): about 40 channels logged only
when they change (gear, lap, sector, pit lane), with a <code>ts</code> column
in seconds on the same clock.</li>
</ul>
<p>Channels with one value per wheel (tyre temperatures, pressures, wear) have
<code>value1</code> to <code>value4</code> columns; the others have
<code>value</code>.</p>

<h2>Reading one with Python</h2>
<pre>import duckdb

con = duckdb.connect("Algarve International Circuit_P_2026-10-30T12_56_57Z.duckdb",
                     read_only=True)
print(con.sql('select * from "channelsList"'))
speed = con.sql('select value from "Ground Speed"').fetchnumpy()["value"]  # km/h, 100 Hz
laps = con.sql('select ts, value from "Lap"').fetchall()  # when each lap starts</pre>

<h2>Things worth knowing</h2>
<ul>
<li>The <code>Lap</code> event fires as each lap starts, and <code>Lap Time</code>
as it ends, with the official time. Lap 0 starts with the recording, usually in
the pits; the last lap usually has no end.</li>
<li><code>Lap Dist</code> is the distance round the lap, back to zero at the
line; <code>Path Lateral</code> and <code>Track Edge</code> give where the car
is across the track.</li>
<li>Pedals come filtered and unfiltered: the unfiltered channels are what your
feet did, before traction control and shift cuts.</li>
<li>Recordings up to about April 2026 store steering as a fraction (−1 to 1)
with no unit; newer ones store it in percent.</li>
<li>There is no yaw (heading) channel, and wind speed and heading have been the
same in every recording we've seen, so they look like placeholders.</li>
</ul>

<h2>Or let LMU Lap Analyser read them</h2>
<p><a href="https://github.com/bgenerowicz/lmu_telemetry">LMU Lap Analyser</a>
is a free app for Windows that reads these files for you. Pick a track and car,
one of your laps and a lap to compare against (one of yours, or a faster
driver's from the <a href="{home}/">online leaderboard</a>), and it shows corner by
corner where you lose time and why, with traces, a track map and both cars'
setups side by side.</p>
"""
