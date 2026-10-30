"""Build the beginner scripting manual, with listings from tested source files."""
from pathlib import Path
import builtins
import io
import keyword
import token
import tokenize
from xml.sax.saxutils import escape

from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle, Image, XPreformatted
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"Scripting User's Guide.pdf"
EXAMPLES=ROOT/'examples/scripts/mcu'
W,H=A4;WIDTH=W-104
styles=getSampleStyleSheet()
for name,font,size,leading,after in (
    ('Body','Helvetica',10,14,8),('Small','Helvetica',8.5,11.5,7),
    ('H1','Helvetica-Bold',19,23,15),('H2','Helvetica-Bold',12,16,8),
    ('Cell','Helvetica',8.8,12,0),('CellHead','Helvetica-Bold',8.8,12,0)):
    styles.add(ParagraphStyle(name=name,fontName=font,fontSize=size,leading=leading,spaceAfter=after))
styles.add(ParagraphStyle(name='CodeSE',fontName='Courier',fontSize=8.6,leading=11.2,
    backColor=colors.HexColor('#f3f5f7'),borderPadding=8,spaceBefore=5,spaceAfter=12))
styles.add(ParagraphStyle(name='Cover',fontName='Helvetica-Bold',fontSize=26,leading=32,alignment=1,spaceAfter=14))
styles.add(ParagraphStyle(name='Center',parent=styles['Body'],alignment=1))
story=[];pages=[];toc_rows=[]

def p(text,style='Body'):story.append(Paragraph(text,styles[style]))
def h(text):p(text,'H2')
def page(title,anchor):
    if pages:story.append(PageBreak())
    pages.append((title,anchor))
    item=Paragraph(title,styles['H1']);item.anchor=anchor;story.append(item)
def steps(items):
    for i,text in enumerate(items,1):p(f'<b>{i}.</b> {text}')
def table(headers,rows,widths=(1,2)):
    cells=[[Paragraph(escape(text),styles['CellHead']) for text in headers]]
    cells += [[Paragraph(str(text),styles['Cell']) for text in row] for row in rows]
    grid=Table(cells,colWidths=[WIDTH*x/sum(widths) for x in widths],repeatRows=1,hAlign='LEFT')
    grid.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#eceff2')),
        ('LINEABOVE',(0,0),(-1,0),.7,colors.black),('LINEBELOW',(0,0),(-1,0),.5,colors.black),
        ('LINEBELOW',(0,-1),(-1,-1),.5,colors.black),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f7f8fa')]),
        ('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),
        ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
    story.extend([grid,Spacer(1,10)])
def note(text):
    table(['Remember'],[[text]],(1,))
def code(source,python=True):
    source=source.rstrip('\n');lines=source.splitlines();spans=[[] for _ in lines]
    palette={token.STRING:'#23723a',token.NUMBER:'#a34a00',tokenize.COMMENT:'#61736a'}
    for item in tokenize.generate_tokens(io.StringIO(source).readline) if python else ():
        color=palette.get(item.type)
        if item.type==token.NAME:
            if item.string in keyword.kwlist:color='#7c3aab'
            elif item.string in dir(builtins) or item.string=='se':color='#006c91'
        if not color:continue
        for row in range(item.start[0],item.end[0]+1):
            if not 1<=row<=len(lines):continue
            start=item.start[1] if row==item.start[0] else 0
            end=item.end[1] if row==item.end[0] else len(lines[row-1])
            spans[row-1].append((start,end,color))
    result=[]
    for line,marks in zip(lines,spans):
        offset=0;parts=[]
        for start,end,color in sorted(marks):
            parts.append(escape(line[offset:start]))
            parts.append(f'<font color="{color}">{escape(line[start:end])}</font>');offset=end
        parts.append(escape(line[offset:]));result.append(''.join(parts))
    story.append(XPreformatted('\n'.join(result),styles['CodeSE']))
def listing(prefix):
    path=next(EXAMPLES.glob(prefix+'*.py'))
    p('Source: examples/scripts/mcu/'+path.name,'Small')
    code(path.read_text(encoding='utf-8'))
def figure(name,caption,height=270):
    image=Image(str(ROOT/'docs/user_guide_assets'/name))
    scale=min(WIDTH/image.imageWidth,height/image.imageHeight)
    image.drawWidth=image.imageWidth*scale;image.drawHeight=image.imageHeight*scale
    story.append(image);p(caption,'Small')

page('Signal Explorer scripting','cover')
story.append(Spacer(1,10))
story.append(Image(str(ROOT/'src/signal_explorer/assets/signal-explorer-splash.png'),width=235,height=235))
story.append(Spacer(1,20))
p('SIGNAL EXPLORER','Cover');p("Scripting User's Guide",'Cover')
p('Basic Python for CAN / CAN FD and MCU testing','Center')
p('Application 0.1.0 | Document revision 1.0<br/>October 29, 2026','Center')
story.append(Spacer(1,14))
p('Learn the editor, send and receive frames, validate responses, check counters and signals, and build repeatable MCU tests. Nine commented example files accompany this guide.','Center')
note('This guide uses the current injected <b>se</b> API. Python scripting is CAN/CAN FD only. The generic MCU protocol is fictional and must match your test firmware; the built-in virtual demo is a separate, smaller protocol.')

page('Contents and example map','contents')
sections=[('1 First run without hardware','quick'),('2 Editor and execution controls','editor'),
    ('3 Python values, bytes and basic control flow','python_values'),('4 CAN API, timing and results','api'),
    ('5 Built-in simulation tests','echo'),('6 MCU contract and bench templates','contract'),
    ('7 Limited UDS single-frame example','uds_scope'),('8 Reusable test patterns','patterns'),
    ('9 Logging, replay and timestamps','evidence'),('10 Troubleshooting and practical limits','troubleshoot'),
    ('Appendix: source files, glossary and references','appendix')]
for label,anchor in sections:
    cell=Paragraph('pending',styles['Cell']);toc_rows.append((cell,anchor))
    story.append(Table([[Paragraph(f'<link href="#{anchor}" color="#124b6a">{label}</link>',styles['Body']),cell]],colWidths=[WIDTH-45,45]))
story.append(Spacer(1,16))
h('Choose the right example group')
table(['Example files','What must be available'],[
    ['01, 02, 03, 04 and 09','Built-in Virtual CAN with Inject demo traffic. Example 04 additionally requires CAN FD enabled before Connect.'],
    ['05, 06, 07 and 08','A firmware/fixture matching section 6 (or a separately built mock peer). Default configuration guard prevents transmission.'],
    ['can_demo_test.py','The existing continuous 321/221 demo opened by File Viewer &gt; Open CAN Simulation Example.'],
],(1.4,3.1))
p('Use the User Guide for adapter setup, wiring and pinouts. Use the Install Guide for Python/vendor driver deployment. This guide focuses on test code and evidence, not a replacement ECU specification.')

page('1 First run without hardware','quick')
steps(['Launch Signal Explorer. In CAN / CAN FD select <b>Virtual (no hardware)</b>, check <b>Inject demo traffic</b> and Connect.',
       'Choose <b>File Viewer &gt; Open CAN Simulation Example</b>. The separate editor leaves the monitor available.',
       'Set Line delay to zero and press Run. Look for TX 321 and RX 221 in the trace. IDs in this guide are hexadecimal.',
       'The first payload is 00 01 02 03 04 05 06 07. The received payload must match; the next request increments each byte.',
       'Try Pause, Resume and Stop. Stop ends the test without disconnecting. Normal simulated engine traffic continues.',
       'For a finite exercise, open examples/scripts/mcu/01_engine_smoke.py. Run it and inspect the PASS checks and final PASSED result.'])
h('What the virtual peer actually does')
table(['ID / direction','Behavior'],[
    ['100 RX','Periodic DemoStatus. Byte zero increments modulo 256.'],
    ['200 RX','Periodic EngineData: RPM, MAF, O2 voltage and lambda. Scales match examples/demo.dbc.'],
    ['300 RX, FD mode','Periodic 32-byte FD telemetry with bitrate switching.'],
    ['321 TX -&gt; 221 RX','Eight-byte standard data-frame echo. FD requests receive FD replies with the same BRS flag.'],
    ['Other MCU/UDS commands','Not implemented by this demo. The test templates need matching firmware or the private mock used by automated tests.'],
])
note('Virtual buses are in-process. Do not start a second Signal Explorer process as the virtual peer, and do not create a can.Bus inside a test script. Use the shared se connection.')

page('2 Editor and execution controls','editor')
figure('scripting.png','Figure 1: the editor paused during a simulated echo exchange.',255)
table(['Control','Use'],[
    ['New / Open / Save / Save As','Edit trusted .py files. Run executes the current editor snapshot, including unsaved changes. Save is atomic; source encoding is retained.'],
    ['Run / Pause / Resume / Stop','One Python test at a time. Editing locks while it runs. Stop rejects further API bus requests; a blocked worker can be terminated after one second.'],
    ['Line delay (ms)','0-1000 ms before each Python source line. Use a small value for learning; zero for timing checks. This changes script execution, unlike log-mode highlighting delay.'],
    ['Follow execution','Scroll to the highlighted source line. Uncheck to inspect code elsewhere. Running is yellow, paused orange, failed red.'],
    ['Script Output','se.log, print, PASS/FAIL checks and final state. Keeps up to 5000 blocks; not a durable report file.'],
    ['Edit / Find','Undo/redo, cut/copy/paste, Select All, case-sensitive Find/Replace; Tab/Shift+Tab indent, Enter preserves indentation.'],
])
p('Common shortcuts: Ctrl+O open, Ctrl+S save, Ctrl+Shift+S Save As, Ctrl+Z undo, Ctrl+Y redo and Ctrl+F Find. Closing an active editor stops its script before releasing the window.','Small')
p('A fresh worker process is not a security sandbox: Python retains the user\'s file/network privileges. Run trusted scripts. Direct driver calls, unmanaged threads or child programs bypass the intended shared CAN controls.','Small')

page('3 Python values and CAN bytes','python_values')
p('Python runs statements from top to bottom. A variable names a value. A comment starts with #. Hexadecimal notation starts with 0x; 0x321 means the same ID shown as 321 in the GUI. Decimal 321 is a different ID.')
code('''# Integers, strings and Boolean flags.
request_id = 0x321
name = "Echo test"
extended = False

# A byte is an integer from 0 to 255 (00 to FF).
payload = bytes([0x12, 0x34, 0, 1, 2, 3, 4, 5])
first_byte = payload[0]       # Indexing starts at zero: 0x12.
first_two = payload[0:2]     # Slice includes 0, excludes 2.
count = len(payload)         # Eight bytes.
se.log(payload.hex(" "))     # 12 34 00 01 02 03 04 05

# Byte order must match the MCU's documented payload.
big = int.from_bytes(first_two, "big")       # 0x1234 = 4660.
little = int.from_bytes(first_two, "little") # 0x3412 = 13330.
encoded = (4660).to_bytes(2, "big")          # b"\\x12\\x34"

# Wrap each byte independently instead of exceeding FF.
incremented = bytes((value + 1) & 0xFF for value in payload)
se.check(count == 8, "Eight-byte payload")''')
h('Choose a representation')
p('<b>bytes</b> is immutable and convenient for frames. <b>bytearray</b> is mutable for building a payload, then bytes(buffer) freezes it. Lists of integers also work with se.can.send. '
  'A text string such as "DE AD" is not a byte array: use bytes.fromhex("DE AD").')
note('The script API frame data is bytes. Verify a received frame is not None and its length is correct before indexing fields. A missing frame is a timeout, not an all-zero payload.')

page('3.1 Decisions, loops and functions','python_flow')
p('Indentation defines a block. Use four spaces. A colon starts an if, for, while or function block. == compares; = assigns. Functions let several tests reuse one validated operation.')
code('''def decode_rpm(data):
    # A helper returns a result to its caller.
    return int.from_bytes(data[0:2], "little") * 0.25

for sample in range(3):
    frame = se.can.recv(2.0, arbitration_id=0x200)
    if frame is None:
        raise RuntimeError("No engine frame")
    se.check(len(frame.data) == 8, "Engine frame length")
    rpm = decode_rpm(frame.data)
    se.log(f"Sample {sample + 1}: {rpm:.0f} rpm")
    se.sleep(100)

# An intentional loop uses API waits so Pause and Stop stay responsive.
# while True:
#     frame = se.can.recv(timeout=None, arbitration_id=0x200)
#     se.log(frame.data.hex(" "))''')
h('Errors and test outcomes')
p('An unhandled exception ends the run as FAILED. se.check(False, description) prints FAIL and raises AssertionError. Catch an exception only when the protocol expects it and you have a deliberate recovery plan. '
  'Catching a failed se.check does not erase that failure from the final result.')
p('PASSED means at least one check ran and no recorded check failed. COMPLETED means a run ended with no checks. STOPPED means it was interrupted; it does not certify the remaining steps. '
  'A while True test has no normal end and usually finishes STOPPED after the operator presses Stop.')
h('Imports and helpers')
p('Standard modules such as struct, json and pathlib are available. Saved scripts can import nearby helper files; each run starts a fresh process/namespace. '
  'The se object is injected, so do not import se or install a package with that name. input() is unsupported and raises EOFError.')

page('4 Signal Explorer CAN scripting API','api')
table(['API','Meaning and units'],[
    ['se.protocol','Currently "CAN". Selecting another main tab does not switch the Python transport.'],
    ['se.session','Run-start metadata snapshot: interface, adapter, native_channel, fd, bitrates and simulated flag. It does not reconfigure hardware.'],
    ['se.is_simulated','True only for the connected Virtual CAN session. Use as a guard for built-in demo scripts.'],
    ['se.log(value)','Append a message to Script Output. print(...) is captured too.'],
    ['se.check(condition, description)','Print PASS/FAIL; a false condition normally terminates execution with an assertion.'],
    ['se.sleep(milliseconds)','Interruptible, pause-aware wait. Not a hardware scheduler.'],
    ['se.can.send(id, data, ...)', 'Send once. Optional keyword flags: extended=False, fd=False, brs=False. Uses the connected adapter, channel and FD validation. Raises on failure.'],
    ['se.can.recv(timeout=1.0, ...)', 'Wait in seconds; None waits until a match or Stop. Returns CanFrame or None. Filters: arbitration_id, extended and direction (rx, tx or all). Default direction is rx.'],
],(1.45,3.55))
code('''se.can.send(0x321, bytes(range(8)))
reply = se.can.recv(2.0, arbitration_id=0x221,
                    extended=False, direction="rx")
se.check(reply is not None, "Response received")
se.check(reply.data == bytes(range(8)), "Correct echo")''')
p('CanFrame fields: arbitration_id, data, timestamp, channel, direction, is_extended_id, is_fd, bitrate_switch, is_error_frame and is_remote_frame. '
  'The receive API excludes error/remote events. There is no built-in DBC signal lookup, periodic-send task, adapter-switch command or full UDS client in se.')

page('4.1 Matching, timing and test results','timing')
h('A local TX entry is not an MCU response')
p('A send uses the main application path, which records one local accepted TX. Receiving with direction="rx" prevents that local echo from satisfying a response check. '
  'Also match the response ID, standard/extended format, length, opcode, sequence or echoed payload as appropriate.')
h('Do not accept stale data')
p('A response can already be queued when recv starts. The script has an independent 2000-event mailbox; unmatched events are consumed as it searches. '
  'Use a transaction field to distinguish a new reply from an old one. Do not overlap requests to different response IDs unless you implement coordinated dispatch: one filtered receive can consume another response.')
h('Use bounded retries')
p('Send a command once, then wait for its response under the firmware\'s deadline. A retry is a new action: repeated reset, erase or output commands may have side effects. '
  'The GPIO template waits up to ten 0.2-second receives for a matching transaction, rather than blindly resending the command.')
h('Pause and line delay')
p('se.sleep and receive timeouts exclude time spent paused. They do not subtract ordinary processing or line delay. Python time.monotonic/time.sleep use real elapsed time and do not inherit this pause model. '
  'For a period test, use message timestamps and zero line delay; do not pause in the measurement interval unless that is part of the test definition.')
h('Timing claims')
p('Loops that send then sleep add processing time to the nominal sleep. A se.sleep(100) request is not a guarantee of an exact 100 ms hardware period. '
  'Use the main repeated-send control or an appropriate hardware scheduler when exact traffic timing is the test stimulus. Log scheduling delay and verify wire timing independently.')
note('Trace pause/filter does not pause scripts or logging. Script Pause does not stop the MCU, other manual transmit controls, replay or periodic demo traffic. Stop cannot retract an already accepted frame or guarantee MCU cleanup.')

page('5 Continuous 321/221 echo demo','echo')
p('The menu example starts at 00 01 02 03 04 05 06 07 and increments every byte only after a verified response. IDs are standard hexadecimal 321 and 221. The built-in peer runs only on Virtual CAN with Inject demo traffic enabled.')
code('''se.check(se.is_simulated, "Use Virtual CAN with demo traffic")
payload = bytes(range(8))
cycle = 0
while True:
    se.can.send(0x321, payload)
    reply = se.can.recv(2.0, arbitration_id=0x221,
                        extended=False, direction="rx")
    se.check(reply is not None, "221 arrived")
    se.check(len(reply.data) == 8, "Eight-byte response")
    se.check(reply.data == payload, "Exact echo")
    cycle += 1
    se.log(f"Cycle {cycle}: {payload.hex(' ')}")
    payload = bytes((value + 1) & 0xFF for value in payload)
    se.sleep(100)''')
h('Expected trace')
table(['Cycle','TX 321 and RX 221 data'],[
    ['1','00 01 02 03 04 05 06 07'],['2','01 02 03 04 05 06 07 08'],
    ['3','02 03 04 05 06 07 08 09'],['After FF','Each byte wraps independently to 00; this is not one 64-bit counter.'],
])
p('The normal periodic 100/200 traffic remains present, plus 300 in FD mode. The standalone source copy is examples/scripts/can_demo_test.py. '
  'This manual\'s shorter listing shows the same control pattern; the packaged menu example also includes extra comments and logging.')

page('5.1 Telemetry smoke test','smoke')
p('Run with the built-in virtual demo. This checks frame arrival, length and decoded ranges for five samples. It is a useful first functional check before testing an MCU response protocol.')
listing('01')
h('What the bytes mean')
table(['Bytes','Demo scaling'],[
    ['0-1','RPM: unsigned little-endian integer multiplied by 0.25.'],
    ['2-3','MAF: unsigned little-endian integer multiplied by 0.01 g/s.'],
    ['4-5','O2 voltage: unsigned little-endian integer multiplied by 0.001 V.'],
    ['6-7','Lambda: unsigned little-endian integer multiplied by 0.001 (not used in this short smoke test).'],
])
p('These are the synthetic examples/demo.dbc values, not standardized OBD-II PIDs. Loading that DBC changes the monitor display; the script decodes raw bytes itself. '
  'For real firmware, copy the correct endian/sign/scale/offset and acceptance limits from the actual interface specification.')

page('5.2 Heartbeat and counter continuity','heartbeat')
p('A heartbeat test can establish that firmware remains alive and advances its sequence field. This listing uses the demo status byte and a broad period tolerance; adapt both fields and limits for a physical MCU.')
listing('02')
h('Interpreting failures')
p('A counter delta of zero may be a duplicate; a delta greater than one can indicate a missed application frame, transport loss or a different publisher. '
  'It does not identify which layer dropped data. Check the trace, logger, driver diagnostics and an independent peer before attributing the failure to firmware.')
p('The period uses recorded RX timestamps, rather than how fast the editor received callbacks. This matters when the UI is busy or buffered. '
  'The demo uses one counter publisher; another sender using ID 100 on the same virtual channel would invalidate that assumption.')
note('For a production test define startup allowance, expected period and jitter, counter width, wrap rule, reset behavior, timeout and whether duplicate frames are permitted. The demonstration limits are not an automotive acceptance standard.')

page('5.3 Payload boundary and walking-bit tests','vectors')
p('All-zero, all-one and alternating patterns exercise payload handling. Walking a single set bit through each byte can expose truncation, byte swapping and incorrect masks. The virtual peer echoes the entire frame.')
listing('03')
h('Extend the vector list deliberately')
p('Add command-specific boundary values such as minimum/maximum sensor counts, signed limits and reserved bits only when the firmware contract permits them. '
  'An echo checks communication/data handling; it does not prove that an actuator moved, an analog input was sampled or a diagnostic routine completed.')
p('For a signed 16-bit field use int.from_bytes(data, "little", signed=True) and check the expected sign. For a bit field use (value &amp; mask) != 0. '
  'Compare complete responses when possible and validate reserved fields separately if the specification defines their values.')
note('The built-in 321 responder accepts exactly eight data bytes and standard IDs. It does not echo arbitrary IDs, extended frames or every CAN FD payload length.')

page('5.4 CAN FD and controlled timeouts','fd_timeout')
p('Enable CAN FD before connecting Virtual CAN. This checks 32-byte telemetry, then an eight-byte FD/BRS request-response exchange. The echo peer\'s eight-byte rule still applies.')
listing('04')
h('A separate no-response exercise')
listing('09')
p('The timeout exercise sends nothing. None means no matching event within the receive deadline; it is not proof that a real MCU rejected a command. '
  'FD payload lengths supported by the application are 0-8, 12, 16, 20, 24, 32, 48 and 64. This build validates mode/length before sending; hardware must support the selected rates and BRS.','Small')

page('6 Define your MCU test contract','contract')
p('The next templates use a <b>fictional MCU application protocol</b>. They are examples for firmware you control, not Astemo production requirements or universal automotive CAN IDs. The built-in demo does not implement it.')
table(['Standard ID / bytes','Fictional firmware contract'],[
    ['500 request, 8 bytes','Byte 0 opcode; byte 1 argument; bytes 2-6 reserved zero; byte 7 transaction (0-255).'],
    ['Opcode 01: set GPIO','Argument is 0 or 1. Reply 501: byte 0=81, byte 1=acknowledged state, bytes 2-3=unsigned little-endian ADC, bytes 4-6 zero, byte 7=transaction.'],
    ['Opcode 03: query boot count','Reply 502: byte 0=B0, bytes 1-2=unsigned little-endian retained boot counter, bytes 3-6 zero, byte 7=transaction.'],
    ['Opcode 02: reset','Firmware retains/increments the boot counter and returns the same 502 boot report after restart, echoing the reset transaction.'],
    ['Unsupported opcode 7E','Reply 501: FF 11 00 00 00 00 00 transaction. This is a custom application response, not UDS.'],
    ['ADC fixture assumption','Independent known midpoint analog stimulus; 12-bit counts, nominal 3.3 V reference, example acceptance 1000-3000. GPIO acknowledgement is not physical pin readback.'],
    ['700 request / 708 response','Separate optional UDS teaching endpoint: normal addressing, Classic CAN single frames, DID 1234 with exactly two data bytes. Not the 500 protocol.'],
],(1.4,3.5))
h('Before enabling a template')
steps(['Confirm your interface specification, rates, IDs, byte order, reset cause/retention behavior and transaction handling. Change the template to match, rather than guessing.',
       'Implement/verify the peer firmware or a private mock. Check a simple request at the independent peer before a sequence test.',
       'Define fixture stimulus and expected response limits. Arrange manual/firmware-timeout cleanup for outputs, since stopping a Python process does not restore the MCU.',
       'Set FIRMWARE_CONTRACT_CONFIRMED = True only in your reviewed copy, then Save As under a project-specific name. Leave the supplied templates unchanged for reference.'])

page('6.1 GPIO acknowledgement and ADC limits','gpio')
p('Requires the section 6 firmware/fixture. This sequence commands 0, 1, then 0, checks the transaction/acknowledgement and validates an independent midpoint ADC stimulus.')
listing('05')
p('The voltage calculation assumes the nominal reference and scaling shown; it is not a calibrated voltmeter. To prove actual GPIO output, add independent fixture readback. '
  'The ten receive waits bound transaction matching to approximately two seconds plus processing; individual waits exclude Pause.','Small')

page('6.2 MCU reset and boot recovery','reset')
p('Requires firmware that retains a boot count and echoes the reset transaction in its post-boot report. This is an application reset command, not the UDS ECUReset service.')
listing('06')
h('Define the reset acceptance criteria')
p('Specify what persists across reset and what the board reports after startup. A counter alone should not be your only production proof: add reset-reason bits, expected firmware version, heartbeat restart and output-default checks if available.')
p('Each boot_report call allows up to three receives to discard stale transactions. The example uses a three-second per-receive timeout after reset, so it is not a strict single three-second overall reboot budget. '
  'For a fixed end-to-end deadline, use a single receive when the firmware guarantees transaction ordering, or a pause-aware deadline helper designed for your runner.')
note('The current scripting API does not control ECU power, external reset pins, a programmer or a USB relay. Those require a separate approved fixture integration. Reset tests should not be treated as a flashing/programming feature.')

page('6.3 Expected rejection and negative tests','negative')
p('A negative test expects a documented rejection while the MCU remains operational. This example sends the reserved unsupported opcode from section 6 and checks the complete rejection prefix and transaction.')
listing('08')
h('Useful next negative-test cases')
p('After defining the firmware behavior, test an out-of-range application value, unsupported mode, wrong transaction or missing prerequisite. Confirm both the rejection response and unchanged state. '
  'Do not equate application-level rejection with malformed CAN frames or electrical error injection; se sends validated frames and has no physical fault-injection API.')
p('Keep a distinct test case for no response: a timeout can mean an intentionally silent command, a bus problem, an ID mismatch or a firmware defect. '
  'A test passes only when its actual expected behavior is explicit. Retrying an invalid command without a bound can hide the first failure.')

page('7 UDS over CAN: scope and framing','uds_scope')
p('UDS describes diagnostic services; ISO-TP carries their payloads over CAN. A small payload can use one Single Frame. Longer transfers need segmentation and flow control. Signal Explorer currently exposes raw CAN frames, not a complete UDS/ISO-TP stack. [R1,R2]')
h('Teaching example: read one two-byte DID')
table(['Layer / bytes','Interpretation'],[
    ['Request ID 700','Example physical 11-bit address; not a required industry-wide ECU ID.'],
    ['03 22 12 34 00 00 00 00','Classic CAN SF length 3, then UDS ReadDataByIdentifier (22), then example DID 1234. Remaining bytes are padding.'],
    ['Response ID 708','Example response address to configure on the matching firmware.'],
    ['05 62 12 34 12 34 00 00','SF length 5; positive SID 62, echoed DID 1234, then this fictional DID\'s two-byte big-endian value 1234.'],
    ['03 7F 22 78 00 00 00 00','Negative-response payload 7F 22 78 means response pending. Wait for the final reply instead of resending the request. [R3]'],
])
p('The positive ReadDataByIdentifier response contains the requested DID and its data. The data encoding/length comes from the ECU specification, not from service 22 alone. [R1]')
h('What this example intentionally does not implement')
p('No First/Consecutive/Flow-Control frames, extended/mixed addressing, CAN FD SF escape format, automatic diagnostic session management, TesterPresent scheduling, P2/P2* negotiation, SecurityAccess, DTC parsing, flashing or transfer routines. '
  'A multi-frame response fails explicitly rather than being partially accepted. A VIN read usually needs more than this two-byte teaching contract.')
p('For an expanded UDS client, keep transport integration on the shared application connection. Opening a second python-can adapter handle inside the script can conflict with receive ownership and logging. '
  'Adding a UDS library to the environment alone does not integrate it with se.')

page('7.1 Single-frame ReadDataByIdentifier','uds_example')
p('This template requires the optional endpoint from section 6. It handles a bounded number of ResponsePending replies and rejects other negative responses. The one-second waits are example values, not negotiated ECU timing.')
listing('07')
p('The private automated test peer sends one 7F 22 78 followed by a valid two-byte DID result. This checks the example control flow; it does not qualify an OEM UDS implementation or a physical ECU.','Small')

page('8 Reusable test patterns','patterns')
h('Keep a project-specific constants block')
code('''REQUEST_ID = 0x321
RESPONSE_ID = 0x221
RESPONSE_TIMEOUT_S = 2.0
INTER_REQUEST_MS = 100

def verify_echo(payload):
    se.can.send(REQUEST_ID, payload)
    frame = se.can.recv(RESPONSE_TIMEOUT_S,
                        arbitration_id=RESPONSE_ID, extended=False)
    se.check(frame is not None, "Echo arrived")
    se.check(frame.data == payload, "Echo payload matched")
    return frame

for value in (0x00, 0x55, 0xAA, 0xFF):
    verify_echo(bytes([value] * 8))
    se.sleep(INTER_REQUEST_MS)''')
h('A practical test-case structure')
steps(['Arrange: connect the correct session, record firmware/database versions and establish fixture state.',
       'Act: send one explicit stimulus with a fresh sequence/transaction when supported.',
       'Assert: require a matching received response, then validate length, fields, state and timing against named limits.',
       'Record: log the measured values and collect an MF4 evidence file separately.',
       'Clean up: return outputs/modes to the defined bench state during normal completion; also provide an independent stop/fault recovery plan.'])
h('Small helpers instead of hidden parallel traffic')
p('Put reusable conversions and response validation in local Python modules saved next to the test. Keep sequence numbers and failure policy visible. '
  'Avoid background threads or a second receive loop unless the runner explicitly manages them; the supported mailbox is owned by one script flow.')

page('9 Logging, replay and timestamps','evidence')
steps(['Before Run, press Start Logging in CAN / CAN FD. The logger captures both shared RX and local TX independently of display filters or pause.',
       'Run the test. Keep the script revision, firmware version, DBC version, adapter/channel, rate and expected outcome with the result.',
       'Stop the script as needed, then Stop Logging and save the MF4. Cancelling the save dialog retains the temporary log for Save Log....',
       'Choose File Viewer &gt; Open Logfile. Inspect read-only rows; connect the intended destination to replay RX, TX or both from the selected recorded bus.',
       'Replay uses recorded gaps. In a request/response test, replaying both directions can transmit historical ECU responses as tester traffic; choose TX only when that is the intended stimulus.'])
figure('log-replay.png','Figure 2: Replay Output and per-row highlighting in log mode.',250)
p('<b>Line delay differs by mode:</b> Python delay slows real source execution; logfile delay samples highlighted rows without changing transmit timing. Replay Pause/Resume preserves the remaining recorded gaps.','Small')
p('<b>Timestamp correction:</b> Intrepid CAN/LIN/Ethernet now anchor hardware RX intervals to PC time on each connection, matching local TX\'s epoch. Earlier logs mixing device/PC clocks can have irrecoverably flattened intervals. '
  'Create fresh recordings after updating. Preflight rejects selected gaps over 24 hours before any TX; valid gaps are not compressed and there is no Skip gaps option.','Small')

page('10 Troubleshooting and practical limits','troubleshoot')
table(['Symptom','Check or next action'],[
    ['NameError: se is not defined','Open/run inside Signal Explorer, not python example.py in a terminal. se is injected by the runner.'],
    ['No CAN session / Run unavailable','Connect CAN/CAN FD in the main window. The selected LIN/Ethernet tab is not a Python transport. Stop another active script.'],
    ['Configuration guard failed','Expected for unreviewed MCU templates. Define matching firmware/fixture and update a saved copy before enabling it.'],
    ['No 221 response','Use Virtual CAN, Inject demo traffic, standard 321 and exactly eight bytes. No physical adapter automatically implements this peer.'],
    ['Timeout / unexpected reply','Match ID, direction, extended format, length and transaction. Check the peer and bus setup. A local TX is not a response.'],
    ['IndexError / wrong decoded values','Check frame is not None and length before indexing. Verify endian, signedness, bit position, scale, offset and units.'],
    ['CAN FD send failed','Connect in FD mode first; verify adapter support, data bitrate and allowed byte count. Classic-only hardware cannot be made FD by a script flag.'],
    ['Script mailbox overflow','The independent queue holds 2000 events, including unrelated traffic. Long pauses/high input can fill it; reduce load or consume promptly. Failure is explicit.'],
    ['Pause remains Pausing','A blocking native call has not returned to a checkpoint. Use se.sleep/se.can.recv. Stop rejects further bus API calls and may kill the worker after one second.'],
    ['Input / local helper import error','input() is unsupported. Save scripts/helpers in the same folder; use the same app environment for optional imports.'],
    ['Passed after a caught assertion?','A failed se.check still makes the final outcome FAILED. Do not use it for expected exceptions without a deliberate failure policy.'],
    ['UDS First Frame / other NRC','The supplied example is single-frame only. Match ECU session/timing and implement a proper integrated transport before expanding it.'],
    ['Timestamp discontinuity','Create a fresh recording after the clock fix. Historical clamped timing cannot be inferred; log opening does not rewrite it.'],
    ['Outputs remain active after Stop','Stop controls the script, not MCU state. Apply the defined fixture/firmware cleanup procedure.'],
],(1.4,3.5))
p('Native API tests are mocked. The examples do not establish electrical fault tolerance, bus-off recovery, wire-level timing, multi-adapter synchronization or compliance certification.','Small')

page('Appendix: files and further reading','appendix')
h('Example source index')
table(['File in examples/scripts/mcu/','Use'],[
    ['01_engine_smoke.py','Five EngineData samples; length, decode and range checks.'],
    ['02_heartbeat_counter.py','Eight counter/period observations with modulo rollover.'],
    ['03_echo_vectors.py','Zero, FF, alternating and walking-bit eight-byte echoes.'],
    ['04_can_fd_echo.py','32-byte FD telemetry and eight-byte FD/BRS echo.'],
    ['05_gpio_adc_template.py','Matching MCU required: GPIO acknowledgement and ADC limits.'],
    ['06_reset_recovery_template.py','Matching MCU required: boot counter before/after reset.'],
    ['07_uds_read_did_sf_template.py','Matching endpoint required: limited UDS single-frame DID read.'],
    ['08_negative_command_template.py','Matching MCU required: documented unsupported-command reply.'],
    ['09_receive_timeout.py','Virtual-only absence/timeout example with no TX.'],
],(2,2))
h('Glossary')
p('<b>MCU</b>: microcontroller. <b>ECU</b>: electronic control unit. <b>DLC</b>: data length code; the API presents a byte count. '
  '<b>DBC</b>: CAN message/signal database. <b>DID</b>: UDS DataIdentifier. <b>NRC</b>: negative response code. '
  '<b>ISO-TP</b>: diagnostic transport on CAN. <b>SF</b>: Single Frame. <b>Transaction</b>: application field used to correlate a response with a request.')
h('Primary technical references')
for key,label,url in (
    ('R1','py-uds: diagnostic services and ReadDataByIdentifier','https://uds.readthedocs.io/en/stable/pages/knowledge_base/service.html'),
    ('R2','Linux kernel: ISO 15765-2 (ISO-TP), framing and addressing','https://docs.kernel.org/networking/iso15765-2.html'),
    ('R3','py-uds: diagnostic messages and negative responses','https://uds.readthedocs.io/en/stable/pages/knowledge_base/diagnostic_message.html'),
    ('R4','Python language tutorial','https://docs.python.org/3/tutorial/')):
    p(f'<b>[{key}]</b> <link href="{url}" color="#124b6a"><u>{escape(label)}</u></link>','Small')
p('UDS/ISO-TP references were checked October 29, 2026. They explain protocols, not an implemented library integration. Application API behavior is defined by src/signal_explorer/scripting/, docs/scripting.md and docs/file-viewer.md.','Small')

page('Appendix: validation and next steps','validation')
h('What was checked for this edition')
p('145 automated tests cover the application and guide examples. All nine new sources compile. The five virtual examples run through the actual separate-process scripting runner with the built-in demo. '
  'The four MCU templates are checked both for their default no-transmit guard and against a private virtual mock firmware peer after enabling the contract flag in the test harness. '
  'The UDS mock emits ResponsePending then a final positive reply; an out-of-range ADC value is also checked to fail the GPIO/ADC test.')
p('These mock peers live in tests/test_scripting_examples.py and are not a second firmware simulator exposed by the GUI. The test harness does not change the shipped templates\' default guard. '
  'No new physical adapter/MCU qualification is claimed by these software checks.')
h('Run checks from the project root')
code('''.\\.venv\\Scripts\\python.exe -m unittest discover -s tests -v
# Only the new guide examples (PowerShell line continuation):
.\\.venv\\Scripts\\python.exe -m unittest discover -s tests `
    -p test_scripting_examples.py -v''',python=False)
p('The backtick continues the second PowerShell command onto the next line. See the Install Guide for environment/Intrepid optional package setup. Documentation regeneration uses reportlab and pypdf; those are not additional runtime dependencies for the examples.','Small')
h('A useful first MCU test suite')
steps(['Start with one status/heartbeat arrival check and a known firmware-version response.',
       'Add correlation, length and signal range checks for one benign request/response.',
       'Add output/ADC checks with independent fixture measurement and defined cleanup.',
       'Add reboot recovery, expected command rejection and explicit timeouts.',
       'Store test and firmware revisions alongside MF4 evidence, then verify on the intended physical adapter.'])
note('SENT, LIN/Ethernet Python scripting, automatic CAPL translation, complete UDS/ISO-TP, fixture power control and integrated test-report export remain future work. Standard Python can write your own report files, but the application does not supply a report API in this release.')
p('Revision 1.0 - October 29, 2026. Companion to User Guide revision 1.2 and Install Guide revision 1.2.','Small')


class ManualDoc(SimpleDocTemplate):
    def afterFlowable(self,item):
        if hasattr(item,'anchor'):
            self.canv.bookmarkPage(item.anchor)
            self.canv.addOutlineEntry(item.getPlainText(),item.anchor,0,False)

def decorate(canvas,doc):
    canvas.saveState();canvas.setStrokeColor(colors.black);canvas.setLineWidth(.5)
    if doc.page>1:
        canvas.setFont('Helvetica-Bold',9);canvas.drawString(52,H-36,"Signal Explorer Scripting User's Guide")
        canvas.line(52,H-41,W-52,H-41)
    canvas.line(52,40,W-52,40);canvas.setFont('Helvetica',8)
    canvas.drawString(52,26,'SIGNAL EXPLORER  |  Revision 1.0  |  October 29, 2026')
    canvas.drawRightString(W-52,26,str(doc.page));canvas.restoreState()

if __name__=='__main__':
    numbers={anchor:index for index,(_,anchor) in enumerate(pages,1)}
    for cell,anchor in toc_rows:cell.__init__(str(numbers[anchor]),styles['Cell'])
    document=ManualDoc(str(OUT),pagesize=A4,leftMargin=52,rightMargin=52,topMargin=61,bottomMargin=54,
                       title="SIGNAL EXPLORER Scripting User's Guide",author='Signal Explorer')
    document.build(story,onFirstPage=decorate,onLaterPages=decorate)
    pdf=PdfReader(str(OUT))
    print(f'Created {OUT}; {len(pdf.pages)} pages; expected {len(pages)}.')
    for index,(actual,(title,_)) in enumerate(zip(pdf.pages,pages),1):
        text=actual.extract_text()
        print(f'{index:02} {len(text):4} {title}')
        assert title in text.replace('\n',' '),f'Unexpected pagination at page {index}: {title}'
    assert len(pdf.pages)==len(pages),'Unexpected overflow page'
    assert len(pdf.pages)==24
