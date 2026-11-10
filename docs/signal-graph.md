# CAN Signal Graph

Load the matching DBC on the CAN screen and open **View → Signal Graph**.
Select the CAN channel and **Received (RX)** or **Sent (TX)**, then choose
a DBC message and signal and click **Add signal**. Keep Exploring running to
collect new samples. Choose a **Time window** of 10, 30, or 60 seconds.

To overlay a signal, choose the new message and signal, then select any
existing signal on the desired graph in **Add To** and click **Add signal**.
Choose **New graph** to plot it separately. Overlays have contrasting trace
colors and legend entries. Signals on one graph share a vertical scale,
so signals with similar units and ranges are easiest to compare.

You can add up to six signals. **Remove selected** removes the selected
trace. **Clear samples** clears the history and keeps the configured signals.
At high frame rates the graph retains the minimum and maximum values in
each 20 ms bucket.

## Plot a CAN log during replay

1. Load the DBC that matches the recording, open the graph, and add the
   signals you want to plot.
2. Select the replay destination CAN channel in the graph and set
   **Direction** to **Sent (TX)**.
3. Start **Playback Log as TX**. The graph uses matching replay frames
   that appear in the CAN trace while Exploring is running. Opening an
   MF4 file for offline inspection alone does not feed the graph.

If **View → Signal Graph** is checked but the panel is missing, use
**View → Reset CAN Tool Layout**, then reopen the graph. This also resets
the other CAN tool dock positions.
