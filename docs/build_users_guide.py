"""Build the user manual with reportlab; run from any directory.

Requires reportlab and pypdf. Screenshot assets use simulated traffic only.
Layout follows the supplied Kvaser guide's engineering-manual conventions;
all prose, branding and app figures are specific to Signal Explorer.
"""
from pathlib import Path
from xml.sax.saxutils import escape
import json
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle, Image, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'users guide.pdf'
ASSETS=Path(__file__).parent/'user_guide_assets'
W,H=A4
WIDTH=W-104
styles=getSampleStyleSheet()
for name,font,size,leading,space in [('Body','Helvetica',10,14,7),('Small','Helvetica',8,10.5,6),
                                    ('H1','Helvetica-Bold',18,22,15),('H2','Helvetica-Bold',12,16,8),
                                    ('Cell','Helvetica',8.8,11.7,0),('CellHead','Helvetica-Bold',8.8,11.7,0),
                                    ('CodeSE','Courier-Bold',8.6,12,9)]:
    styles.add(ParagraphStyle(name=name,fontName=font,fontSize=size,leading=leading,spaceAfter=space))
styles.add(ParagraphStyle(name='Cover',fontName='Helvetica-Bold',fontSize=28,leading=34,alignment=1,spaceAfter=16))
styles.add(ParagraphStyle(name='Centered',parent=styles['Body'],alignment=1))
story=[];pages=[];table_number=0;page_refs=[]


def page_range(first,last=None):
    paragraph=Paragraph('pending',styles['Cell'])
    page_refs.append((paragraph,first,last))
    return paragraph

def p(text,style='Body'):
    story.append(Paragraph(text,styles[style]))

def h(text):p(text,'H2')

def page(title,anchor):
    if pages:story.append(PageBreak())
    pages.append((title,anchor))
    para=Paragraph(title,styles['H1']);para.anchor=anchor
    story.append(para)

def bullets(items):
    for item in items:p('&#8226; '+item)

def steps(items):
    for i,item in enumerate(items,1):p(f'<b>{i}.</b> {item}')

def note(text):
    t=Table([[Paragraph('<b>Note</b> - '+text,styles['Body'])]],colWidths=[WIDTH])
    t.setStyle(TableStyle([('BOX',(0,0),(-1,-1),.6,colors.black),('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#f5f6f7')),
                          ('LEFTPADDING',(0,0),(-1,-1),9),('RIGHTPADDING',(0,0),(-1,-1),9),('TOPPADDING',(0,0),(-1,-1),7)]))
    story.extend([t,Spacer(1,10)])

def table(headers,rows,widths=None,caption=None,compact=False):
    global table_number
    table_number+=1
    cells=[[Paragraph(str(x),styles['CellHead']) for x in headers]]
    cells += [[x if isinstance(x,Paragraph) else Paragraph(str(x),styles['Cell']) for x in row] for row in rows]
    widths=[WIDTH*w/sum(widths) for w in widths] if widths else [WIDTH/len(headers)]*len(headers)
    t=Table(cells,colWidths=widths,repeatRows=1,hAlign='LEFT')
    t.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e9ecef')),
        ('LINEABOVE',(0,0),(-1,0),.7,colors.black),('LINEBELOW',(0,0),(-1,0),.5,colors.black),
        ('LINEBELOW',(0,-1),(-1,-1),.5,colors.black),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f6f7f8')]),
        ('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),
        ('TOPPADDING',(0,0),(-1,-1),3 if compact else 5),('BOTTOMPADDING',(0,0),(-1,-1),3 if compact else 5)]))
    story.append(t)
    p(f'Table {table_number}: '+(caption or 'Reference.'),'Small')

def code(lines):
    text=escape(lines).replace(' ','&#160;').replace('\n','<br/>')
    p(text,'CodeSE')

def figure(name,caption,max_height=None):
    img=Image(str(ASSETS/name));scale=WIDTH/img.imageWidth
    if max_height:scale=min(scale,max_height/img.imageHeight)
    img.drawWidth=WIDTH;img.drawHeight=img.imageHeight*scale
    story.append(img);p(caption,'Small')

def source(text):p('Source: '+text,'Small')

# 1 ---------------------------------------------------------------------------
page('Signal Explorer','cover')
story.append(Spacer(1,36))
p('SIGNAL EXPLORER','Cover')
p('User\'s Guide','Cover')
p('CAN / CAN FD, LIN and Ethernet desktop workbench','Centered')
story.append(Spacer(1,22))
figure('can.png','Figure 1: Signal Explorer running virtual CAN FD traffic with the demo DBC.')
story.append(Spacer(1,16))
p('Application version 0.1.0 - File Viewer and scripting release<br/>Document revision 1.2 - October 29, 2026','Centered')
note('This guide describes the current source build. Hardware driver integration is not a physical-bus qualification. '
     'Native LIN and ValueCAN Ethernet await powered bench verification. Passive PC Ethernet capture has been checked. '
     'Ethernet TX is implemented but awaits physical testing. SENT, CAN XL, signal plotting and installers are not implemented.')
p('Read this guide alongside Install guide.pdf. Install guide covers deployment; this manual covers operation, hardware selection, verification and recovery.','Small')

# 2 ---------------------------------------------------------------------------
page('Contents and document scope','contents')
table(['Section','Pages','Use it for'],[
    ['<link href="#release_today">October 29 release changes</link>',page_range('release_today'),'File Viewer, scripts, demo echo, branding and timestamp correction.'],
    ['<link href="#s1">1. System prerequisites &amp; hardware profile</link>',page_range('s1','hardware'),'PC requirements, rates, FD, isolation and support scope.'],
    ['<link href="#s2">2. Mandatory driver/software setup</link>',page_range('s2'),'Preparation before USB or bus connection; Npcap.'],
    ['<link href="#s3">3. Connection &amp; verification</link>',page_range('s3','ethernet_verify'),'CAN, LIN and Ethernet simulation/hardware checks.'],
    ['<link href="#s4">4. Graphical interface reference map</link>',page_range('s4','file_viewer_log'),'Every current column and control, with screenshots.'],
    ['<link href="#s5">5. Practical workflows</link>',page_range('s5','scripting_workflow'),'DBC/LDF, sending, Ethernet tests, MF4 capture/replay.'],
    ['<link href="#s6">6. Troubleshooting &amp; fault codes</link>',page_range('s6','timestamp_faults'),'CAN/LIN/Ethernet faults, driver errors and recovery.'],
    ['<link href="#s7">7. Appendix: adapter pinouts</link>',page_range('s7','ethernet_pins'),'CANable, Kvaser, PEAK, Vector, Intrepid and Ethernet.'],
    ['<link href="#cli">7. Appendix: command line and sources</link>',page_range('cli','revision'),'Launch commands, environment settings and source register.'],
], [2.7,.7,3.8], 'Navigation for this edition.')
h('How to read the support statements')
p('<b>Implemented</b> means there is an application code path. <b>Mock tested</b> means API behavior was checked without electrical traffic. '
  '<b>Physically observed</b> means the specific observation stated was made; it does not imply all rates, adapters or features are qualified.')
p('This release has 145 passing automated tests. Existing CANable reception is distinct from native LIN/Ethernet mock coverage. '
  'ValueCAN4-2EL VE0245 and five PC Ethernet interfaces were discovered on the development PC. Passive capture on its active Ethernet port worked; '
  'no application-generated physical Ethernet TX or powered ValueCAN Ethernet/LIN verification was performed.')
h('Manual conventions')
p('Control names are bold. CAN and LIN IDs and data bytes are hexadecimal; rates and intervals are decimal unless marked otherwise. '
  'Connector tables refer to pin numbers molded into the connector, not wire colors. NC means not connected; a blank vendor assignment is identified explicitly.')
p('This manual uses the supplied Kvaser Leaf Light R v2 guide as a layout reference: numbered sections, restrained typography, figures, tables, notes and running page headers. '
  'Kvaser logos and product photographs are not reused. The Signal Explorer screenshots contain simulated traffic.')

# 3 ---------------------------------------------------------------------------
page('October 29, 2026: release changes','release_today')
story.append(Image(str(ROOT/'src/signal_explorer/assets/signal-explorer-splash.png'),width=130,height=130))
p('Application version remains 0.1.0. Document revision 1.2 covers the current source build; it does not introduce an installer.','Small')
table(['Change','Current behavior'],[
    ['File Viewer menu','Replaces Scripts. New/open Python scripts, the CAN simulation example and Open Logfile are available.'],
    ['Python editor and runner','Separate window, syntax colors, line numbers, Find/Replace, Save/Save As, Run/Pause/Resume/Stop, line delay, Follow execution and Script Output.'],
    ['Continuous demo test','Standard 321-hex requests contain eight bytes; the virtual peer replies on 221 hex with the same data. The script verifies, increments every byte and repeats until stopped.'],
    ['Logfile viewer','Read-only MF4 event rows. Replay RX, TX or both through the connected protocol, with timed highlighting, Pause/Resume/Stop and Replay Output.'],
    ['Replay timing','1x recorded gaps. Log line delay samples highlighting only; Python line delay slows actual source execution. Playback ownership is shared with the main protocol tab.'],
    ['Icon and splash','Black SE icon with red S and white E. Startup splash adds white SIGNAL and EXPLORER; it closes when the main window opens. The title-bar icon stays the SE mark.'],
    ['Intrepid timestamps','CAN, LIN and Ethernet RX hardware intervals are anchored to host time on each connection, aligning them with local TX timestamps. Invalid replay clock jumps fail preflight.'],
], [1.45,4.7], 'Changes implemented October 29, 2026.')
p('The separate <b>Scripting User\'s Guide.pdf</b> introduces Python and provides commented, downloadable source examples for CAN/MCU tests.','Small')

page('1 System prerequisites & hardware profile','s1')
h('1.1 PC and software prerequisites')
bullets(['Use Windows 10/11 x64 with <b>64-bit Python 3.12</b> for the documented setup. The package declares Python 3.11+, but that is not a cross-version test guarantee.',
         'Keep the project in a writable folder and create its own .venv. Allow disk space for Python packages, temporary captures and saved MF4 files; no sustained-load RAM/storage minimum has been qualified.',
         'The main packages are PySide6-Essentials, python-can[mf4], asammdf, pyserial, cantools, uptime and ldfparser. Intrepid adds python-ics and filelock.',
         'Obtain the correct bus description, connector/cable part number, baud rates and a known-good second node before physical testing. Hardware detection alone does not prove a healthy bus.'])
h('1.2 Rates actually selectable in this application')
table(['Setting','Current choices and effect'],[
    ['CAN arbitration bitrate','125, 250, 500 or 1000 kbit/s; default 500 kbit/s. The GUI does not offer custom sample-point or arbitrary bitrate editing.'],
    ['CAN FD data bitrate','0.5, 1, 2, 4, 5 or 8 Mbit/s; default 2 Mbit/s. Must be at least the arbitration rate; the adapter/driver may reject a choice.'],
    ['LIN baud rate','2400, 4800, 9600, 10400, 19200 or 20000 bit/s. The demo uses 19200. Loading an LDF requires an available rate.'],
    ['Repeat interval','CAN/Ethernet: blank sends once; 1 to 2147483647 whole milliseconds repeats. LIN uses schedule slot delays.'],
    ['Ethernet link / frame size','ValueCAN4-2EL: 10/100 PHY. PC rate is controlled by Windows/NIC settings. App MTU: 1500 bytes; Ethernet II with one VLAN tag. No speed/duplex or CAN FD setting applies to Ethernet.'],
], [1.5,4.6], 'GUI settings, not vendor hardware maxima.')
h('1.3 CAN FD and electrical prerequisites')
p('Enable <b>CAN FD</b> before connecting, then choose <b>FD frame</b> for each outgoing FD message. <b>BRS</b> switches to the data bitrate. '
  'Valid FD byte counts are 0-8, 12, 16, 20, 24, 32, 48 and 64. Classic CAN is limited to 8 bytes. Standard IDs use 11 bits; Extended ID uses 29.')
p('SLCAN and gs_usb remain Classic CAN paths in this build, even if a particular board/firmware advertises FD. ValueCAN3 and Leaf Light R v2 are Classic-only. '
  'A classic-only node may disturb FD traffic; confirm the entire network configuration before enabling FD.')
note('CAN requires correct end termination and a functioning peer. LIN requires a suitable bus supply and one master. '
     'Isolation is a hardware property, not an app setting. A test voltage is not a permissible continuous working voltage.')

# 4 ---------------------------------------------------------------------------
page('1.4 Supported hardware and isolation','hardware')
table(['Adapter / family','Application support','Hardware profile / isolation'],[
    ['CANable original / 2.0 / Pro','Classic CAN via SLCAN; candleLight via gs_usb. No LIN.','Board/clone dependent. Pro is advertised with 2.5 kV isolation; do not transfer that rating to original/2.0/clones. Use actual silkscreen and board documentation. [H1]'],
    ['Vector VN1610','CAN and FD via XL; no LIN on its CAN transceivers.','Two capacitively decoupled 1051cap CAN channels on one D-Sub. Vendor: CAN up to 1 Mbit/s, FD 5 Mbit/s (8 with suitable wiring/transceivers). No isolation voltage asserted here. [H5]'],
    ['Vector VN1630 / VN1630A / VN1640 / VN1640A','CAN/FD where fitted hardware supports it; native XL LIN on active LIN-capable piggies.','Transceiver-dependent. 7269mag example: 50 V insulation rating; other piggies differ. Match exact revision; the app label CANXL does not add CAN XL. [H5,H6]'],
    ['Intrepid ValueCAN3','Classic CAN via neoVI; no FD or LIN.','Two CAN channels, USB isolation; exact isolation test voltage not stated in the cited profile. [H4]'],
    ['Intrepid ValueCAN4-1/-2/-4','CAN/FD via neoVI. No LIN on these variants.','ValueCAN4 family has isolated USB. Do not assume per-channel isolation. Hardware FD up to 8 Mbit/s subject to model/timing. [H3]'],
    ['Intrepid ValueCAN4-2EL','CAN/FD; experimental native LIN 1 and Ethernet 1.','USB supports CAN; external 12 V for LIN/Ethernet. 10/100 PHY. USB isolation; no voltage rating claimed. Cable variant changes pins. [H2,H3]'],
    ['Kvaser U100-C','CAN/FD via CANlib; no Kvaser LIN backend.','CAN 10-1000 kbit/s; FD up to 8 Mbit/s. Reinforced isolation tested at 5000 VAC rms for 60 s. No internal CAN termination. [H7]'],
    ['Kvaser Leaf Light R v2','Classic CAN via CANlib; FD must stay off. Not physically tested here.','50-1000 kbit/s, USB powered, galvanically isolated; supplied manual gives no numerical isolation rating. No internal termination. [H8]'],
    ['PEAK PCAN-USB Pro FD','CAN/FD via PCAN-Basic; experimental LIN via PLIN.','Hardware: CAN 25-1000 kbit/s, FD data up to 12 Mbit/s; app stops at 8. CAN ports isolated up to 500 V from USB/LIN. LIN pair: 500 V from USB/CAN, not each other. Each LIN port needs 8-18 V. [H9]'],
    ['Virtual CAN / LIN / Ethernet','Hardware-free functional examples.','No electrical connector, isolation, bus-off behavior or physical timing simulation. PC Ethernet capture uses the NIC; its isolation/rate follow its own hardware manual.'],
], [1.6,2.1,3.3], 'Named hardware profiles. Vendor limits can exceed the GUI choices in section 1.2.')
p('Native LIN for Vector, Intrepid and PEAK has mock coverage; powered timing, response removal, error reporting and interoperability still need bench qualification. '
  'Kvaser U100-C and PEAK hardware were not available for physical tests. Other devices accepted by a generic vendor backend require their own manual and validation.','Small')

# 5 ---------------------------------------------------------------------------
page('2 Mandatory driver/software setup','s2')
h('2.1 Before plugging the adapter or bus in')
steps(['Identify the exact adapter, firmware personality (SLCAN vs candleLight), piggy type and cable. Save the matching pinout and bus parameters.',
       'Install the manufacturer\'s signed Windows driver and matching 64-bit API runtime first, following that installer\'s prompts. If it asks for USB connection during setup, follow that instruction. Keep vehicle/bus wiring disconnected.',
       'Create the Python environment and install Signal Explorer. For development use the editable install below. Vendor DLLs are separate and are not shipped in this repository.',
       'After installation, connect USB and verify enumeration in Device Manager and the vendor utility. Connect the physical bus only after checking the correct cable map and supply.'])
code('py -3.12 -m venv .venv\n.\\.venv\\Scripts\\python.exe -m pip install -e .\n.\\.venv\\Scripts\\python.exe -m pip install -e ".[intrepid]"')
table(['Backend','Required software before connection'],[
    ['CANable SLCAN','Correct serial firmware and Windows USB serial driver if needed. The COM number is machine-specific. App serial speed is 115200; this is not the CAN bitrate.'],
    ['CANable / candleLight','Compatible gs_usb firmware plus python-can[gs-usb], USB/WinUSB/libusb setup appropriate to the board. Changing the USB driver can break SLCAN access.'],
    ['Vector CAN / LIN','Vector hardware drivers plus XL Driver Library. If not found, use Vector DLL in the CAN tab to choose vxlapi64.dll and restart.'],
    ['Intrepid CAN / LIN / Ethernet','Intrepid driver, 64-bit icsneo40.dll and [intrepid] optional packages. Ethernet: current python-ics with ExtraDataPtr fixes; 1!26.3.9+ recommended, 1!26.3.17 checked.'],
    ['PC Ethernet (Windows)','Install Npcap from npcap.com. The app loads installed System32/Npcap/wpcap.dll. No extra Python capture package. Admin-only Npcap installs may require elevation. [E1]'],
    ['Kvaser CAN','Kvaser Windows drivers with CANlib runtime. Both U100-C and Leaf Light R v2 use CANlib; a CANlib virtual channel is not physical hardware.'],
    ['PEAK CAN','PEAK driver and 64-bit PCAN-Basic API (PCANBasic.dll).'],
    ['PEAK LIN','PEAK LIN driver, PLIN Manager service and PLIN-API (PLinApi.dll). PCAN-Basic alone cannot supply LIN.'],
], [1.4,4.7], 'Optional vendor components are independent.')
note('Signal Explorer does not update adapter firmware. Record the installed driver/firmware versions before changing them and use the vendor\'s update utility if required.')

# 6 ---------------------------------------------------------------------------
page('3 Connection & verification: CAN','s3')
h('3.1 Hardware-free application check')
steps(['Launch the app, choose <b>Virtual (no hardware)</b>, leave Inject demo traffic checked, and Connect.',
       'Expect IDs 100 and 200 to change. Load examples/demo.dbc to decode DemoStatus and EngineData. Disconnect, enable CAN FD and reconnect to add 32-byte ID 300.',
       'Send ID 200 with DE AD BE EF and a blank interval. Expect one local TX row. This validates the UI/send path, not external electrical reception.'])
note('python-can Virtual buses exchange within one Python process. Two separately launched Signal Explorer processes are not a virtual two-node loopback. '
     'There is no internal-loopback or listen-only toggle in the current CAN GUI.')
h('3.2 Verify hardware in the vendor console')
table(['Adapter','Independent check before using Signal Explorer'],[
    ['Vector','Open Vector Hardware Config / Vector Hardware Manager. Confirm serial, bus transceiver and active channel. Use the vendor loop test if installed, following its required two-channel cable/termination setup.'],
    ['Intrepid','Use neoVI Explorer / ICS Device Manager or Vehicle Spy to confirm serial, CAN/LIN enables and bitrates. The EL needs external supply for LIN. Stop scripts and release the device before app connection.'],
    ['Kvaser','Use Kvaser Device Guide to confirm model/channel and a nonzero firmware version. CanKing provides an independent traffic check. Leaf R v2 green PWR should be steady; yellow CAN indicates traffic. [H8]'],
    ['PEAK','Use PEAK-Settings to check CAN Hardware and LIN Hardware, then PCAN-View or PLIN-View Pro as appropriate. Release the LIN channel before Signal Explorer. [H9]'],
    ['CANable','Confirm the expected COM/USB device and firmware. Close serial terminals or other bus tools before SLCAN connection. USB enumeration does not establish CAN traffic.'],
], [1.1,5], 'Device-console checks; these utilities are not embedded in Signal Explorer.')
h('3.3 Physical two-node CAN verification')
p('Use a known-good second adapter or ECU, CAN-H to CAN-H, CAN-L to CAN-L and the correct bus-side reference. Put 120-ohm termination at both ends; '
  'about 60 ohms between H and L with power off is a useful check when no additional paths affect the reading. Match arbitration rate and FD/BRS/data rate on both nodes.')
p('Refresh devices, select the exact channel and Connect. Send a known frame from the peer and confirm RX; send from Signal Explorer and confirm it at the peer. '
  'A lone normal CAN transmitter needs another active node to acknowledge it. TX rows alone are not proof of ACK or successful peer receipt. [F1]')

# 7 ---------------------------------------------------------------------------
page('3.4 Connection & verification: LIN','lin_verify')
h('Virtual LIN first')
steps(['Choose the LIN tab. Leave Virtual LIN (simulated), Monitor, 19200 and Simulated peer selected. Connect LIN; expect header/response events for IDs 10 and 20.',
       'Disconnect, select Master and reconnect. Send Header 20 or Start Schedule; Stop Schedule independently of Pause Exploring.',
       'Disconnect, choose Slave and reconnect with Simulated peer. EngineStatus responses transmit only when the peer master requests them. Change response data and Apply Responses.'])
h('Native adapter connection')
table(['Choice','Expected selection / setup'],[
    ['Intrepid ValueCAN4-2EL','Refresh and choose the detected serial, e.g. VE0245, LIN 1. Model type is checked; ValueCAN4-2 and ValueCAN3 are excluded. Temporary baud/resistor changes use no EEPROM writes.'],
    ['Vector VN16xx / XL LIN','Refresh and choose an active LIN-capable channel with the correct piggy. Channel index/mask are taken from discovery. CAN-only VN1610 channels are not listed. Release other tools\' initialization access.'],
    ['PEAK PLIN / PCAN-USB Pro FD','Refresh and choose the detected PLIN channel, not a guessed PCAN CAN channel name. Close other PLIN clients on that LIN channel.'],
], [1.8,4.4], 'Native LIN choices. USB detection is not a powered transmission test.')
h('Powered bench acceptance sequence')
steps(['Use the exact appendix cable map. Provide the vendor-specified bus supply, common bus reference and the proper master pull-up/diode arrangement. LIN does not use CAN\'s two 120-ohm terminators.',
       'Use CANalyzer/VN1640 as master and Signal Explorer/ValueCAN4-2EL as Slave. Load matching LDF, rate, length and checksum settings. Confirm returned bytes on the external tool.',
       'Reverse roles when the external tool\'s license/configuration supports responder simulation. Confirm Master Header, local responses, schedules, unchecking/removing responders and stopping without unintended TX.',
       'Check no-response, checksum errors, pause vs logging, stop/disconnect cleanup and MF4 import. Intrepid dedicated Wake is disabled; use the external master to wake that bus.'])
note('Native Vector CAN/LIN and Intrepid CAN/LIN/Ethernet sessions on the same device are mutually exclusive within this app until shared native sessions are implemented. '
     'Separate devices can stay connected in separate tabs. Powered native LIN behavior is not qualified.')

# 8 ---------------------------------------------------------------------------
page('3.5 Ethernet connection & verification','ethernet_verify')
h('Choose a transport; the simulator stays available')
table(['Choice','Prerequisites / profile'],[
    ['Simulated Ethernet','No hardware or capture driver. In-process UDP/ARP/VLAN/raw test traffic and an optional simulated peer.'],
    ['PC Ethernet | port name | MAC | up/down','Windows and Npcap. Lists wired and virtual Ethernet interfaces, including unplugged ports; Wi-Fi and loopback are excluded. NIC rate, isolation and connector follow its own manual, not a generic app rating.'],
    ['Intrepid ValueCAN4-2EL | serial | Ethernet 1','Intrepid driver/API plus python-ics; external 12 V and the matching Ethernet breakout. 10/100 PHY; preserve/configure existing speed/duplex in neoVI Explorer. CAN-only ValueCAN variants are excluded. [H2]'],
], [2.0,4.1], 'Ethernet transport selection. A capture session being open is not link verification.')
steps(['Open <b>Ethernet</b>. The first display scans adapters; <b>Refresh adapters</b> rescans while disconnected. Simulation is the default. Discovery never opens or transmits on an interface.',
       'For a software check, leave Inject demo traffic and Simulated peer replies checked and Connect Ethernet. Expect UDP telemetry, ARP, VLAN UDP and raw 88B5 frames. Send a UDP test to obtain a synthetic ACK response.',
       'For PC capture, select the intended port/MAC and inspect link status. Enable Promiscuous capture only when needed, then Connect Ethernet. Capture starts automatically; verify incoming traffic from a known peer. No app-generated TX is needed for this check.',
       'For ValueCAN, disconnect CAN/LIN or other tools using that unit. Provide 12 V and verify PHY/link in the vendor console; release the console and Connect Ethernet. Device detection over USB alone is insufficient.',
       'For a physical TX bench test, use a peer capture tool, choose a raw test frame, enter the peer destination MAC and suitable source/data, then Send once. Confirm the complete bytes at the peer before testing repeat or replay. This acceptance test remains pending.'])
h('What capture can and cannot establish')
p('PC direction is inferred from the selected NIC source MAC and includes other PC applications\' traffic. Promiscuous mode sees only frames delivered to that port; use a switch mirror or TAP for other switch ports. '
  'ValueCAN uses native RX records and locally accepted TX. TX is not proof of peer reception. [E1,E2]')
note('Native hardware never receives the simulator\'s automatic demo traffic or ACK replies. Send, repeated send and playback transmit on the selected physical port. '
     'No PC IP-address changes, DoIP activation-line control or automatic ARP/IP stack are provided.')

page('4 Graphical interface reference map','s4')
figure('can.png','Figure 2: CAN/CAN FD view. Demo traffic and decoded values are synthetic.')
table(['Region','What it contains'],[
    ['A. Above protocol tabs','Adapter, Channel, Bitrate, Inject demo traffic, Connect/Disconnect, Clear, Start/Pause Exploring, Refresh devices and the conditional Vector DLL control.'],
    ['B. Below CAN tab','CAN FD/data bitrate, driver notes, filter field, DBC controls and Auto-scroll.'],
    ['C. Central trace','Timestamped history or fixed latest-value rows. Resize column boundaries to reveal long Data or Signals fields. Horizontal scroll exposes FD/BRS/ESI.'],
    ['D. Under trace','Outgoing message ID, Extended ID, FD frame, BRS, data, interval and Send/Stop.'],
    ['E. Recording area / bottom','Logging/save/playback, recorded-bus and direction selectors, logger/playback status and CAN RX/TX/visible-row counts.'],
], [1.45,4.7], 'Read the CAN window from top to bottom.')
p('<b>Graphs:</b> there are no plots, gauges, signal charts, bus-load graphs or time cursors in this build. Decoded signals are text in the trace. '
  'Use the saved MF4 in a suitable external viewer when plots are needed. This guide does not invent controls from vendor sample tools.')

# 9 ---------------------------------------------------------------------------
page('4.1 CAN columns and controls','can_ref')
table(['Column','Meaning'],[
    ['Timestamp','Seconds with six decimal places from the message timestamp; displayed as a number, not time-of-day. Physical precision depends on the source, not the number of printed digits.'],
    ['Counts','Cumulative displayed observations for channel + ID + standard/extended format since Clear. RX and local TX for that key contribute; not a dropped-frame count.'],
    ['Bus Number','Native channel identifier, e.g. COM3, an index or PCAN channel name. Not automatically the MDF Recorded bus value.'],
    ['Message ID (hex) / Ext','Arbitration ID; Ext = Y for 29-bit format, blank for 11-bit. Leading zeros do not change the ID.'],
    ['Direction / DLC / Data','RX or local TX; DLC is byte count (python-can convention), not the encoded FD DLC nibble. Data is raw hex bytes.'],
    ['DBC Message / Signals','Matched message name and decoded signal values/units; unknown IDs stay raw, short/invalid payloads show a decode error.'],
    ['FD / BRS / ESI','Y marks CAN FD, bit-rate switch, or the received sender\'s error-state indication. ESI is not a bus-off indicator for the local adapter and is not user-editable TX.'],
], [1.5,4.6], 'CAN trace columns in left-to-right order.')
table(['Control','Behavior'],[
    ['Adapter / Channel / Bitrate','Select the backend and detected device/network before Connect. Manual channel entry is available on CAN. Connection settings lock while connected.'],
    ['CAN FD / Data bitrate','Connection-level FD mode and data rate. Off/unavailable on unsupported backends; frame-level FD is separate.'],
    ['Inject demo traffic','Virtual CAN only; generates the demo frames roughly every 100 ms.'],
    ['Connect / Disconnect','Open or close the selected bus; disconnect also stops repeated sending and playback and finalizes active logging.'],
    ['Clear / Refresh devices / Vector DLL','Clear resets trace/history/counters, not a saved log. Refresh enumerates devices. Vector DLL saves an XL DLL path; restart to load it.'],
    ['Start Exploring / Pause Exploring','Connection starts exploring. Pause freezes trace and display counters while RX draining, TX and logging continue. Paused frames are not backfilled.'],
    ['Auto-scroll (all messages)','Checked: all observed frames in time order, last 10000 retained. Unchecked: one updated row per channel/ID/format. Toggle preserves recent history.'],
    ['Apply filter / Clear filter','Apply hex ID/range expression; Enter also applies. Clear restores all IDs. A filter affects display only; invalid input keeps the previous filter.'],
    ['Load DBC / Unload DBC','Load decodes existing and new rows; unload returns to raw. No DBC signal-value transmit editor exists.'],
], [1.8,4.3], 'Connection, trace and decoding controls. See sections 5.2 and 5.4 for sending and recording.')

# 10 --------------------------------------------------------------------------
page('4.2 LIN graphical reference map','lin_map')
figure('lin.png','Figure 3: Virtual LIN Master after schedule traffic. The same protocol controls bind to native transports.')
table(['Region','What it contains'],[
    ['A. Connection bar','LIN adapter, detected channel and Refresh for native hardware; Role, Baud rate, Simulated peer for Virtual, Connect LIN/Disconnect LIN.'],
    ['B. Trace toolbar','Start/Pause Exploring, Clear, Auto-scroll (all events), hex ID filter, Apply/Clear filter, Load LDF and the loaded filename.'],
    ['C. Trace and direct operations','Event rows, Frame ID input, Send Header, Start/Stop Schedule, Sleep, Wake and Virtual-only Inject next fault.'],
    ['D. Response table','ID, Length, Publisher, Publish locally checkbox, Data (hex), Checksum selector; Apply Responses and Add Frame.'],
    ['E. Schedule / recording','LDF schedule selection, Use LDF Schedule, editable slots, Add/Remove Slot; shared MF4 logger/playback and LIN status line.'],
], [1.5,4.6], 'LIN region map.')
p('A disabled control normally reflects the selected role, disconnected state, active schedule or active playback. Changing tabs does not disconnect the other protocol. '
  'The bottom LIN status shows its own RX/TX/visible-event counts; the CAN status line is hidden while LIN is selected.')

# 11 --------------------------------------------------------------------------
page('4.3 LIN columns, roles and buttons','lin_ref')
table(['Column(s)','Meaning'],[
    ['Timestamp / Counts / Bus Number','Event timestamp in seconds; cumulative count per channel + six-bit ID + event kind; physical LIN channel number. Header and response are separate events.'],
    ['Frame ID (hex) / PID (hex)','Six-bit ID 00-3F versus the parity-protected byte. Enter the six-bit ID when requesting a header, not the PID. TX IDs 3E/3F are reserved.'],
    ['Direction / Event','RX/TX for the observed part; event kind can be frame, header, no_response, checksum_error, parity_error, collision, sleep or wake.'],
    ['Length / Data / Checksum / Model','Observed payload byte count and hex bytes, checksum when available, classic/enhanced model. IDs 3C/3D always use classic checksums; responses require 8 bytes for these diagnostic IDs.'],
    ['Publisher / LDF Frame / Signals','Simulator publisher label where available, matched LDF frame and decoded signals. Native publisher identity may be blank; LIN frames do not carry a node name.'],
], [2.0,4.1], 'LIN trace columns in left-to-right order.')
table(['Control','Behavior / role restriction'],[
    ['Role / Baud rate / Connect LIN','Monitor: no local response or header requests. Master: headers/schedules and selected local responses. Slave: buffer updates, responds to external headers. Disconnect before role/rate changes.'],
    ['Start/Pause Exploring / Clear / Auto-scroll / filter','Trace behavior matches CAN; fixed LIN rows also distinguish event kind. Filter uses six-bit IDs 00-3F. Logging and schedules are independent of trace pause.'],
    ['Load LDF','Disconnect first. Loads unconditional frames, signal decoding, initial values, publishers and supported schedules. No separate Unload LDF button. Demo LDF loads automatically at startup.'],
    ['Frame ID / Send Header','Master only; ID must have a defined response length. Sends a request; an external or local publisher supplies data.'],
    ['Start/Stop Schedule','Master only; cycles configured slots. Stop is separate from Pause Exploring. Desktop timers are approximate; slots must meet the wire-time budget.'],
    ['Sleep / Wake','Master Sleep stops scheduling then requests sleep. Vector/PEAK have native Wake. Intrepid dedicated Wake is disabled. Monitor cannot send either.'],
    ['Fault selector / Inject next fault','Virtual only: checksum, no response, PID parity or response collision on the next transaction. No physical error injection is offered.'],
    ['Publish locally / Data / Checksum / Apply Responses','Select IDs owned locally and edit payloads; Apply updates buffers in an active Master/Slave session. Native checksum-model changes require disconnect.'],
    ['Add Frame','Disconnected only; enter six-bit hex ID and decimal length, e.g. 21, 4. Adds a manual unconditional definition; it does not write an LDF file.'],
    ['Use LDF Schedule / Add Slot / Remove Slot','Copy chosen LDF schedule into editable slots; add a default slot or remove the selected row. Each row is hex ID + decimal delay (ms). Not an on-disk schedule editor.'],
], [2.0,4.1], 'LIN connection, editing and transmission controls.')

# 12 --------------------------------------------------------------------------
page('4.4 Ethernet graphical reference map','ethernet_map')
figure('ethernet.png','Figure 4: Ethernet tab with synthetic VLAN/UDP traffic; no physical transmission was used for this figure.')
table(['Region','What it contains'],[
    ['A. Above protocol tabs','Ethernet adapter selector, Refresh adapters and Connect/Disconnect Ethernet; Inject demo traffic, Simulated peer replies and Promiscuous capture options. Connection settings lock while open.'],
    ['B. Notes / trace toolbar','Transport details and scan results; Start/Pause Exploring, Clear, Auto-scroll, search, EtherType, RX/TX selector and Apply/Clear filter.'],
    ['C. Trace / selected frame','Chronological frames or latest flow rows. Scroll horizontally for Summary and Data. Select a row to show full raw frame bytes below the trace.'],
    ['D. Test frames / transmit editor','Preset selector, Load Test Frame, Inject Test RX; source/destination MAC, EtherType, VLAN/PCP, hex payload, interval and Send/Stop.'],
    ['E. Recording / status','Logging, save and playback; recorded bus/direction; logger and playback status; counts, driver drops, rejected frames and display drops on native sessions.'],
], [1.5,4.6], 'Ethernet layout; complete column and editing reference follows.')
p('The simulator is selected by default. Demo/peer/Inject Test RX apply only to it. Promiscuous capture applies only to PC Ethernet. '
  'The active tab has its own monitor and recording controls; changing tabs does not disconnect other devices.')

page('4.5 Ethernet columns & editor','ethernet_ref')
table(['Column(s), left to right','Meaning'],[
    ['Timestamp / Counts / Bus Number','Unix seconds; cumulative observations per retained flow; current Ethernet bus is 1. A flow includes bus, direction, MAC pair, EtherType, VLAN and decoded IP endpoints/ports.'],
    ['Direction / Source MAC / Destination MAC','RX/TX, then source and destination layer-2 addresses. PC capture TX is inferred from NIC source MAC; locally submitted TX is reported after API acceptance.'],
    ['EtherType (hex) / VLAN / PCP','Inner Ethernet II type; blank VLAN/PCP for untagged frames. VLAN ID 0-4094, priority 0-7. Example 0800 = IPv4; 0806 = ARP; 88B5 = raw test.'],
    ['Length / Protocol / Summary / Data (hex)','Length excludes preamble, SFD and FCS. Driver-provided padding is retained; NIC captures may omit it. Summary decodes basic IPv4/UDP/ARP; Data is bytes after EtherType, including captured padding.'],
], [2.0,4.1], 'Ethernet trace columns; row selection reveals the complete MAC header and payload.')
table(['Control','Behavior'],[
    ['Start/Pause Exploring / Clear','Pause freezes display/counters, not capture, TX or logging. Resume has no backfill. Clear resets trace and display counts only.'],
    ['Auto-scroll (all frames)','Checked: last 10000 chronological frames. Unchecked: up to 10000 latest flows. Flow counts reset on Clear or eviction; both caches are bounded.'],
    ['Search / EtherType / RX + TX','Search matches all space-separated terms across MAC, IP, protocol, summary, VLAN and data hex, case-insensitively. EtherType is hex. Apply/Enter activates; invalid input keeps the old filter. Direction selector applies immediately.'],
    ['Load Test Frame / Inject Test RX','Load populates the editor; PC presets use its NIC source MAC. Inject adds one synthetic RX and is simulation-only. Preset IP/destination addresses are test values, not discovered peers.'],
    ['Source / Destination MAC','Six hex octets separated by colons or hyphens. Source must be nonzero and unicast. Broadcast destination is allowed.'],
    ['EtherType / 802.1Q VLAN / PCP','Ethernet II type 0600-FFFF; do not enter a VLAN TPID as the inner type. Optional single tag, ID 0-4094 and PCP 0-7. No stacked tags.'],
    ['Payload / Interval / Send / Stop','Raw payload hex, up to 1500 bytes. Blank interval sends once; positive integer ms repeats with Stop. Short TX is padded to 60 bytes, no FCS. Inputs lock during repeat/playback.'],
], [1.85,4.25], 'Ethernet controls. Log controls follow section 5.4; procedures are in section 5.5.')
note('Editing raw IPv4/UDP payload does not repair checksums. Presets generate valid checksums; NIC offload can make outbound capture checksums appear invalid. '
     'No Ethernet signal database, TCP session editor, SOME/IP or DoIP decoder is provided.')

page('4.6 File Viewer: Python scripts','file_viewer_script')
figure('scripting.png','Figure: Python editor running the virtual 321/221 echo example, paused on a highlighted line.',280)
table(['Control','Meaning'],[
    ['File Viewer menu','New Python Script, Open Python Script..., Open CAN Simulation Example and Open Logfile. Python always uses the connected CAN/CAN FD session.'],
    ['File / Edit','Open/New, Save/Save As, undo/redo, cut/copy/paste, Select All and case-sensitive Find/Replace. Run uses the editor snapshot, including unsaved edits.'],
    ['Run / Pause / Resume / Stop','One script process at a time. Pause freezes script checkpoints and its API waits; Stop cancels further bus operations without disconnecting. Editing locks while running.'],
    ['Line delay / Follow execution','Python delay: 0-1000 ms before each source line; changes actual test execution. Follow scrolls to the executing line. Yellow=running, orange=paused, red=failure.'],
    ['Script Output','Print/log messages, PASS/FAIL checks and final outcome. The console retains at most 5000 text blocks. A failed check normally stops the run.'],
], [1.55,4.6], 'Script-mode reference. Full API and MCU examples are in the Scripting User\'s Guide.')
p('Scripts share the existing send/receive/logging path and never need another can.Bus. LIN/Ethernet Python scripting, CAPL conversion and a full UDS/ISO-TP client are not implemented.','Small')

page('4.7 File Viewer: logfile replay','file_viewer_log')
figure('log-replay.png','Figure: read-only log viewer paused during replay. RX/TX labels describe the original recording.',280)
table(['Control','Meaning'],[
    ['Open Logfile','Loads a single-protocol CAN/CAN FD, LIN or Signal Explorer Ethernet MF4. May be opened without a connected adapter. Contents are read-only; Save/Save As are disabled.'],
    ['Recorded bus / direction','Choose the source BusChannel and RX only, TX only or RX and TX. Selected CAN/Ethernet events become TX on the currently connected destination.'],
    ['Replay / Pause / Resume / Stop','Validate the entire selection, then transmit at 1x timing. Pause excludes paused time from the schedule. Stop leaves the connection open; Replay restarts from the beginning.'],
    ['Line delay / Follow execution','In log mode, delay controls highlight refresh only. It never delays transmission. Follow scrolls to the most recently sent original log row.'],
    ['Replay Output','Load, preflight, progress, completion, failure, pause and stopping messages. The main monitor and logger remain independent.'],
], [1.55,4.6], 'Log mode. Existing protocol-tab playback controls remain available.')
p('Display cap: first 100000 rows or about 32 MiB of text; the output reports truncation, but replay reads the entire file. No ASC/BLF/PCAP import, seek, speed or automatic loop. LIN retains Master-header/Slave-buffer semantics.','Small')

page('5 Practical workflows: CAN and DBC','s5')
h('5.1 Decode a database and filter IDs')
steps(['Connect Virtual with demo traffic or a correctly configured physical bus. Click Load DBC and choose examples/demo.dbc or the correct project database.',
       'Expect ID 100 = DemoStatus, 200 = EngineData, and 300 = EngineFD only in FD demo mode. Engine signals include RPM, MAF, O2 voltage and lambda. These are examples, not OBD-II requests.',
       'Enter <b>100, 200-2FF</b> in ID filter and Apply filter (or Enter). IDs are hexadecimal; spaces/commas separate entries. This example hides 300. Clear filter returns all rows.',
       'Uncheck Auto-scroll to compare the latest bytes/signals by ID. Recheck to return to retained time history. Pause Exploring freezes the trace but does not stop logging or TX.'])
h('5.2 Send a frame once or every 100 ms')
table(['Field','Classic example','FD example'],[
    ['Connection','FD unchecked, 500 kbit/s','FD checked before Connect; 500 kbit/s arbitration, 2 Mbit/s data'],
    ['Send ID (hex)','200','300'],
    ['Extended ID','Unchecked for 11-bit 200','Unchecked; use it only for an actual 29-bit ID'],
    ['FD frame / BRS','Both off','FD frame on; BRS on if the network uses rate switching'],
    ['Data (hex)','DE AD BE EF','00 01 02 03 04 05 06 07 08 09 0A 0B (12 bytes)'],
    ['Interval (ms)','Blank for once; 100 to repeat','Same interval rule'],
    ['Send / Stop','Send transmits immediately, then repeats if an interval exists. Click Stop to end.','Stop before editing the payload or connection.'],
], [1.4,2.25,2.45], 'Raw frame editor examples; identifiers/payloads must be suitable for the bench.')
p('The interval must be a positive integer, not 100.5 or 0. The first send occurs immediately. Repetition locks its input fields and stops on send error, disconnect or close. '
  'Playback also stops repeated sending. This is host-timed traffic, not deterministic hardware scheduling.')
note('TX indicates the local adapter accepted a send; it does not prove another CAN node acknowledged or consumed it. Verify the bytes at the peer. '
     'There is no manual RTR/error-frame transmit control, DBC signal encoder or automatic physical-ECU response logic. The virtual 321/221 demo is a separate synthetic peer.')

# 13 --------------------------------------------------------------------------
page('5.3 LIN Master and Slave workflows','lin_workflow')
h('Load an LDF and run as Master')
steps(['Disconnect LIN, Load LDF, and confirm its available baud rate. The bundled demo has MasterCommand at 10 (2 bytes) and EngineStatus at 20 (4 bytes).',
       'Select Master. Verify Publish locally matches your node. Demo MasterCommand starts with 19 01; EngineStatus is normally supplied by the simulated or external slave.',
       'Connect LIN. Type 20 and Send Header to request EngineStatus. Select the demo schedule and Use LDF Schedule: IDs 10 and 20 have 50-ms slots.',
       'Start Schedule and inspect the events. Stop Schedule ends requests; Pause Exploring only freezes display. Sleep also stops the schedule before issuing the sleep command.'])
h('Configure Slave response buffers')
steps(['Disconnect and choose Slave. For Virtual leave Simulated peer enabled to supply the master. For native, arrange a powered external master using the same LDF.',
       'Select local response IDs. For demo ID 20 enter four bytes, e.g. 08 07 6E 19, and keep the LDF checksum model. Connect; Apply Responses after any data edit.',
       'Observe TX only after a matching header. In Virtual mode unchecking the local publisher can cause the demo peer to supply that ID; native tests need an intentionally quiet alternative publisher.',
       'Uncheck an ID and Apply Responses to remove its responder. Verify on the external tool that no stale native response remains. This specific behavior still needs powered qualification on Intrepid firmware.'])
h('Manual frames and schedule limits')
p('Use Add Frame while disconnected to define <b>21, 4</b>. Edit its Data and publisher checkbox. Add Slot, enter ID 21 and a suitable delay. '
  'The slot minimum uses 1.4 x nominal header/response wire time: <b>1.4 x (34 + 10 x (length + 1)) x 1000 / baudrate</b> milliseconds. '
  'At 19200 with a four-byte response that is about 6.13 ms; a 50-ms slot is a convenient demo choice.')
p('Unsupported LDF schedule commands fail explicitly. Sporadic/event-triggered schedules, LIN diagnostic transport/NAD services and K-Line are not implemented. '
  'Changing the table does not save a new LDF. Real timing and electrical behavior must be verified separately.')
note('A LIN master can also publish a response; Slave mode does not transmit complete unsolicited frames. Use exactly one master and one intended publisher for each response ID.')

# 14 --------------------------------------------------------------------------
page('5.4 MF4 logging and timed playback','recording')
h('Record and save')
steps(['Connect the relevant protocol and Start Logging. Both RX and TX observations are captured before display filtering/pause. A log belongs to that protocol/channel; separate tabs create separate files.',
       'Stop Logging drains/finalizes the MDF4 recording and opens the save dialog. Enter a filename; .mf4 is appended when necessary.',
       'Cancel keeps a finalized temporary recording. Use Save Log... to save it later; a pending unsaved recording blocks starting a new log. Save failures retain the source file.',
       'Disconnect or closing finalizes active logging too. Close waits for recording/playback work and save handling. Temporary files are not a promised power-loss recovery system.'])
h('Playback controls and meaning')
table(['Control / status','Effect'],[
    ['Start Logging / Stop Logging','Begins capture or finalizes it. Filtering, Clear and Pause Exploring do not erase or limit logged traffic.'],
    ['Save Log...','Enabled when a finalized recording is pending; save it after cancellation or a failed destination write.'],
    ['Recorded bus','Selects the file\'s MDF BusChannel, 0-255. Signal Explorer currently maps its single recorded source to bus 1, regardless of native channel text.'],
    ['RX + TX / Recorded RX only / Recorded TX only','Selects source observations for replay. Every selected replayable CAN/Ethernet frame becomes TX on the current output; recorded RX is not a passive receive.'],
    ['Playback Log as TX / Stop Playback','CAN/Ethernet: preflight, then best-effort 1x relative timing through the connected output. Virtual Ethernet says Playback Log as Simulated TX. No seek, speed or loop controls.'],
    ['Playback Headers / Replay Response Buffers','LIN Master replays headers with current response configuration. LIN Slave replays timed changes to selected local buffers; the external master still triggers wire TX. Monitor cannot replay.'],
    ['Logger / playback status lines','Show active, finalizing, pending/saved, preflight, progress, completion, skipped events and maximum scheduling delay, or failure details.'],
], [2.1,4], 'Shared recording panel and protocol-specific replay labels.')
p('Use the same protocol and compatible FD mode; do not replay an arbitrary MF4 and expect unrelated signal groups to become bus frames. CAN error frames are not transmitted. '
  'Recording while replaying captures the new session\'s observations. RX+TX may contain related requests/responses or duplicates: choose the appropriate direction.')
p('CAN uses the python-can MF4 schema; LIN uses LIN_Frame plus custom SignalExplorer_LINEvent events. Ethernet uses custom SignalExplorer_EthernetFrame records, with hardware/simulation, TX semantics and FCS exclusion in session metadata. '
  'Ethernet is an MDF 4.10 container, not standardized ASAM Ethernet bus-event interoperability. Round trips are not independent ASAM certification. Clock domains are not synchronized across separate adapters.')

page('5.5 Ethernet test, capture & replay','ethernet_workflow')
h('Exercise the complete path in simulation')
steps(['Choose Simulated Ethernet, enable Inject demo traffic and Simulated peer replies, then Connect Ethernet. Watch UDP telemetry, ARP request, VLAN UDP and raw 88B5 frames rotate every 100 ms.',
       'Select <b>VLAN UDP</b> and <b>Load Test Frame</b>. Expect type 0800, VLAN 100 and PCP 3. Send with a blank interval: one TX and a synthetic RX containing ACK should appear.',
       'Enter <b>100</b> in Interval (ms) and Send. The button becomes Stop; press it to end repetition. To inspect the trace without stopping the sender, use Pause Exploring.',
       'Enter <b>EngineRPM 192.0.2.1</b> in search and <b>0800</b> in EtherType, then Apply filter. All terms must match. Use RX only to inspect replies; Clear filter restores all rows.',
       'Start Logging, pause or filter the trace, and send a few test frames. Stop Logging and save an .mf4 file. Both RX/TX are recorded despite the display state.'])
h('Replay into simulation, then a controlled hardware bench')
steps(['Disconnect; uncheck Inject demo traffic and Simulated peer replies before reconnecting for a replay-only run. Choose Recorded bus 1 and the intended recorded direction.',
       'Click Playback Log as Simulated TX and choose the Ethernet log. Selected RX and TX both become transmitted frames at 1x relative timing. Stop Playback interrupts remaining sends; starting playback stops repeated Send.',
       'For hardware, disconnect and choose the intended PC port or powered ValueCAN4-2EL. Demo/peer controls become disabled. Verify the link and destination with an independent peer tool.',
       'Load/edit a test preset for that network and Send once. Preset addresses 192.0.2.1/192.0.2.2 and 02:00:00:00:00:01/02 are examples; they do not discover or address your real ECU automatically.',
       'After peer verification, log/replay a suitable bench capture using Playback Log as TX. Replay preserves recorded source/destination bytes and adds minimum TX padding when a NIC capture omitted it. It does not rewrite MAC/IP addresses for the selected hardware.'])
h('Recording compatibility and boundaries')
p('The Ethernet schema stores BusChannel, Direction, Length and RawBytes. The valid bytes are selected by Length; existing simulation MF4 files still replay. '
  'CAN/LIN logs and arbitrary signal-only MF4 files are rejected by Ethernet playback. Hardware send success means driver/API acceptance, not received bytes at a peer.')
note('The simulator\'s ACK is a synthetic response to valid UDP or raw 88B5 sent to its peer. Hardware has no such automatic reply. '
     'No PCAP import/export, auto-ARP or TCP/IP session stack is implemented. Desktop timing is best-effort, not hardware scheduling.')

# 15 --------------------------------------------------------------------------
page('5.6 Python CAN and MCU test workflow','scripting_workflow')
h('Start with the built-in virtual peer')
steps(['Connect Virtual CAN with Inject demo traffic enabled. Optionally enable CAN FD before connecting.',
       'Choose <b>File Viewer &gt; Open CAN Simulation Example</b> and Run. Expect TX 321 with 00 01 02 03 04 05 06 07, then RX 221 with the same eight bytes.',
       'After each matching reply the script increments every byte, wrapping FF to 00, and waits 100 ms before the next request. The loop ends only on Stop or a failed check/error.',
       'Pause/Resume in the editor to inspect a line while normal demo traffic continues. Stop keeps the adapter connected. Monitor pause/filter does not stop the script.',
       'Start Logging before running to capture both sides. Stop Logging and save, then use File Viewer &gt; Open Logfile to inspect or replay a chosen direction.'])
h('Move to your MCU firmware contract')
p('Define IDs, standard/extended format, byte order, command/response layout, sequence fields, response deadlines and expected values before changing a script. '
  'The built-in simulator implements the 321/221 echo and periodic demo traffic; it is not a generic MCU, UDS server or hardware fault injector.')
p('The companion Scripting User\'s Guide includes GPIO/ADC, reboot, negative-command and limited single-frame UDS templates. Their example IDs are fictional. '
  'They refuse TX until FIRMWARE_CONTRACT_CONFIRMED is set after reviewing the matching firmware and fixture. Source files are in examples/scripts/mcu/.')
h('Timing and result interpretation')
p('Use receive timeouts in seconds and se.sleep in milliseconds. Leave Python line delay at zero for timing tests. A positive local TX entry is API acceptance; verify a matching received payload or an independent physical peer.')
p('A stopped script does not restore MCU outputs, cancel already accepted frames or reset the ECU. Use a fixture cleanup step and firmware timeout for output-control tests. '
  'Normal Python exceptions fail the run; checks already marked FAIL remain failures even if the script catches their exception.')
h('Open and replay a saved test')
steps(['Open the correct protocol MF4 via File Viewer, connect the intended destination and select Recorded bus plus recorded direction.',
       'Use Replay, Pause/Resume and Stop in the viewer. The main tab\'s Stop Playback can also stop it. Only one replay per protocol can run.',
       'Use Follow execution for row tracking. Log-mode line delay changes highlighting only; selected recorded gaps remain unchanged.'])

page('6 Troubleshooting: CAN and drivers','s6')
table(['Symptom / fault','Interpretation and action'],[
    ['Connected; no RX','First verify another node is transmitting. Match channel and bitrate, check the exact connector map/power, then Clear filter and Start Exploring. USB alone creates no CAN traffic.'],
    ['TX increases; peer sees nothing / ACK error','Local TX acceptance is not physical success. Check a second active CAN peer, correct termination and a compatible mode/rate. A silent/listen-only peer may not acknowledge.'],
    ['Stuff error / bit, form or CRC error','A bitstream/protocol rule was violated. Check bitrate/sample timing, FD/BRS/data-rate compatibility, H/L polarity, wiring quality, stubs, ground reference and termination. Use a vendor analyzer for fault subtype.'],
    ['Error passive / bus-off','The controller has confined itself after repeated errors. Stop repeat/playback, inspect the cause and recover using the vendor procedure; then disconnect/reconnect as appropriate. No dedicated reset/bus-off recovery control exists in this GUI.'],
    ['CAN FD send rejected','Connect in FD mode and select FD frame; use a supported payload size. Data rate must be at least arbitration rate. The actual hardware/firmware can still reject the combination.'],
    ['Known unsupported FD channel','Leaf Light R v2 and ValueCAN3 are Classic-only. Some detected channels explicitly report no FD capability. Do not infer FD support from a vendor dropdown name.'],
    ['Access denied / port in use','Close the other serial app or incompatible vendor session. For Vector/Intrepid, disconnect this app\'s other protocol session on the same physical device.'],
    ['Missing DLL / empty device list','Check 64-bit runtime and USB driver separately. Use Refresh after hotplug; restart after changing DLL paths. An installed API with no hardware can legitimately list no channels.'],
    ['DBC decode error / raw-only ID','Confirm database version, standard vs extended ID and correct payload length. A decode error does not imply the physical frame had a CRC error. Unmatched IDs remain raw.'],
    ['Receive failed / Send failed','Keep the entire error text including native code. A send error stops repetition; a receive failure needs attention and may disconnect the session. Correct the hardware/API issue before retrying.'],
], [1.8,4.3], 'CAN and connection recovery. Error taxonomy reference [F1].')
note('The CAN trace does not expose an error-type column, live controller error counters, bus-load graph or normalized vendor fault-code dashboard. '
     'A displayed ESI bit describes the sender of that FD frame, not proof that the local controller is bus-off. Inspect driver diagnostics/LEDs and a vendor console for controller state.')
p('Disconnect power before resistance measurements. Normal CAN operation in Signal Explorer is not a selectable listen-only mode. The hardware may acknowledge traffic even when Exploring is paused.','Small')

# 16 --------------------------------------------------------------------------
page('6.1 LIN, logging and overload faults','lin_faults')
table(['Event / message','What to check or do'],[
    ['no_response','No complete response followed the header. Check publisher selection, power/reference, ID/length and a running master. A native timeout is not a valid checksum.'],
    ['checksum_error / parity_error','Check classic/enhanced model and protected-ID handling. Enter a six-bit ID; do not type PID into Frame ID. IDs 3C/3D require classic checksum.'],
    ['collision','More than one response publisher or a TX/RX mismatch. Ensure one master and the intended publisher per ID. Virtual injection is an example, not physical fault generation.'],
    ['Vector access denied / XL error','Other software may hold initialization access; release that physical LIN channel. Check installed LIN piggy, active LIN capability and bus setup. Raw XL status is retained in error text.'],
    ['PEAK PLIN error 0x....','Check PLIN Manager, PLinApi.dll, channel ownership and supply. Codes belong to PLIN, not PCAN-Basic. Consult [H10] with the exact function/code.'],
    ['Intrepid receive/API errors','Capture stops on reported API errors. Verify device driver/firmware, LIN supply and the 2EL model. Close other clients; check neoVI Explorer after app disconnect.'],
    ['LIN cleanup failed','Read the complete error. The app attempts remaining cleanup. Stop the external master and inspect the vendor device state before reuse; do not assume failed cleanup removed every responder.'],
    ['Recording queue overflow','The 100000-event writer queue filled. Capture stops and a finalized partial log is marked incomplete. Save it, reduce offered load and check disk/CPU throughput; display filters do not reduce capture load.'],
    ['Save failed - log retained','Choose a writable location with space and a different file if the source is playing. Use Save Log...; do not discard the retained temporary file before saving.'],
    ['No replayable events / preflight error','Check file protocol/schema, Recorded bus, direction and selected LIN response IDs. FD or length/model incompatibility prevents replay; no selected TX should occur before successful preflight.'],
], [1.8,4.3], 'LIN/recording symptoms and recovery.')
h('Native codes are not universal')
p('Vector XL status 10 and PLIN return 3 mean an empty receive queue in their respective APIs; the adapters consume these normally. '
  'Other numeric codes must be interpreted in the matching API manual, including the failing function name. Intrepid LIN flag examples used by this build: '
  '0x04000000 = ID parity, 0x02000000 = TX/RX mismatch, 0x40000000 = receive timeout, 0x80000000 = no slave data (StatusBitField2). These are bit masks, not OS error numbers.')
p('Trace retention of 10000 rows and paused display are intentional limits, not evidence of transport drops. There is no universal dropped-frame counter. '
  'Driver overrun warnings, explicit logger overflow and replay scheduling-delay messages provide different evidence; save the messages with the capture. Native LIN polling errors/overruns stop the session.')

# 17 --------------------------------------------------------------------------
page('6.2 Ethernet capture & fault recovery','ethernet_faults')
table(['Symptom / status','Interpretation and action'],[
    ['PC port listed; capture unavailable','Npcap is missing, inaccessible or cannot enumerate that interface. Install current Npcap, then Refresh adapters. Admin-only installs may need elevation. Do not copy an arbitrary wpcap.dll into the project.'],
    ['No PC Ethernet ports listed','This release lists Windows Ethernet-type interfaces only, including virtual ones. Wi-Fi/loopback are excluded. Check Device Manager and Windows network adapters; reconnect/enable the port and rescan.'],
    ['Npcap open failed / permissions','Check installed Npcap service/driver and the selected adapter. Use the installer\'s capture permissions policy; run elevated only if required. Refresh after adapter removal or replacement.'],
    ['DLT_EN10MB error','The capture interface returned a non-Ethernet link-layer format. Select a wired Ethernet interface; raw Wi-Fi/radiotap and loopback captures are not accepted.'],
    ['Session open; no traffic','An open handle is not link verification. Check link LEDs/status, physical cable and a known transmitting peer. ValueCAN needs external 12 V. Clear filters and Start Exploring. Capture cannot see traffic hidden behind other switch ports without a mirror/TAP.'],
    ['ValueCAN device already open / in use','Disconnect CAN or LIN on that serial and release Vehicle Spy/neoVI Explorer. A shared native multi-protocol handle is not implemented. Different devices may be used independently.'],
    ['Driver drops','Npcap cumulative buffer/interface drops. Reduce incoming load and investigate capture/PC performance. Intrepid shows n/a because its binding does not expose this cumulative statistic; n/a is not zero loss.'],
    ['Display drops','The bounded 2000-frame UI queue overflowed. Display missed events; the logger is fed separately. Check logger overflow messages separately rather than assuming the MF4 also lost those frames.'],
    ['Unsupported/error frames','Rejected captures are not logged: e.g. truncation, jumbo payload, stacked VLAN, 802.3 length frames, invalid source MAC or Intrepid CRC/length errors. This build supports Ethernet II, MTU 1500 and one VLAN tag.'],
    ['Bad IPv4/UDP checksum summary','Raw edits can invalidate a checksum. Load a fresh preset or repair the packet bytes. Outbound NIC offload may also show incomplete checksums in host capture; verify at an independent peer.'],
    ['Intrepid RX/API or TX error','The session stops and logging finalizes; repeat and replay stop. Preserve the function/status text. Check power, link and PHY setup, release the device and reconnect after correcting the cause.'],
    ['No replayable Ethernet events','Choose a Signal Explorer Ethernet MF4, Recorded bus 1 and a direction present in the log. CAN/LIN or unrelated MF4 groups do not become Ethernet frames. Hardware and older simulation logs share this custom schema.'],
], [1.8,4.3], 'Ethernet symptoms. Native error codes are API-specific, not universal bus fault codes.',compact=True)
p('Physical Ethernet errors are not CAN stuff-errors or bus-off. CRC/length rejections, native API errors, driver buffer loss, display queue loss and recorder queue overflow are distinct conditions. '
  'No line-rate lossless performance or independent ASAM Ethernet interchange qualification is claimed.','Small')

page('6.3 Timestamp and script recovery','timestamp_faults')
h('Why an older Intrepid log can jump by years')
p('The October 23 test log recorded Intrepid RX close to a 2007-based device clock while local TX used the PC\'s 2026 clock. '
  'At row 4936 the saved time jumped forward by 625204447.174 seconds (about 19.8 years). Chronological correction then clamped many older RX timestamps forward; '
  '28656 corrections were recorded in that file\'s session metadata. The original intervals cannot be reconstructed from the flattened timestamps.')
h('Correction in this build')
p('On each Intrepid CAN, LIN or Ethernet connection, the first hardware RX timestamp is anchored to PC receipt time. Later RX events retain their hardware time differences, '
  'using the same host-epoch basis as local TX. Reconnection creates a fresh anchor; a backwards device-clock step over one second re-anchors. '
  'This includes initial driver latency and does not synchronize multiple adapters or provide PTP/GPS accuracy.')
p('Restart the application and make a fresh recording. Existing MF4 files are not rewritten. Preflight rejects selected gaps above 24 hours with the original row and gap before any replay TX. '
  'Bounded platform waits prevent the Windows timeout-overflow error. There is no Skip gaps control and valid gaps are not compressed.')
table(['Symptom','Action'],[
    ['This example requires Virtual CAN','Choose Virtual, Inject demo traffic and Connect, or use a reviewed hardware-specific test instead of the built-in demo.'],
    ['No 221 reply / test timeout','Enable demo traffic for 321/221. For a physical MCU, check ID, format, payload contract, rate, channel and peer response. Local TX alone is insufficient.'],
    ['Receive mailbox overflow','The script queue holds 2000 events independently of the display. Stop, reduce test load or consume promptly; long pauses can fill it. The test fails explicitly.'],
    ['Pause remains Pausing','A blocking native call must return to a Python checkpoint. Prefer se.sleep and se.can.recv over time.sleep or direct driver calls.'],
    ['Stop or disconnect','Further script bus operations are rejected. Stop is cooperative, then the worker may be killed after one second. Finally blocks are not guaranteed; inspect MCU state separately.'],
    ['Logfile Replay unavailable','Wait for loading; connect the matching protocol, select a replay-capable LIN role if applicable, and stop any other playback for that protocol.'],
], [1.9,4.25], 'Scripting and timestamp recovery.')

page('7 Appendix: CANable, Kvaser and PEAK','s7')
p('Use the connector\'s molded pin numbers and the vendor view orientation. Male/female mating faces and solder faces are mirrored. '
  'Do not assign power to an unused pin from a generic CAN diagram. Tables below apply only to the named hardware; custom cables/board clones override them.')
h('7.1 CANable / CANable 2.0 / Pro / candleLight')
table(['Terminal marking','Connection / restriction'],[
    ['CANH / H','Bus CAN-H.'],['CANL / L','Bus CAN-L.'],['GND','Bus-side reference per the board\'s isolation arrangement.'],
    ['5V, if fitted','Output only on original CANable / 2.0; leave unconnected unless intentionally powering a compatible 5-V target. Never apply vehicle battery voltage here.'],
    ['Termination switch / jumper','Enable only if the board is at a CAN bus end and the network needs that terminator. Position/order varies by board revision.'],
], [1.6,4.5], 'Label-based terminal map; there is no universal numbered CANable connector. [H1]')
h('7.2 Nine-pin D-Sub: Kvaser and PCAN-USB Pro FD')
table(['Pin','U100-C / Leaf Light R v2','PEAK PCAN-USB Pro FD'],[
    ['1','NC','NC / optional +5-V output configuration'],['2','CAN-L','CAN-L'],['3','GND','CAN-GND'],
    ['4','NC','LIN'],['5','Shield','LIN-GND'],['6','NC','LIN-GND'],['7','CAN-H','CAN-H'],['8','NC','Not assigned'],
    ['9','NC','LIN VBAT (8-18 V input, per LIN connector)'],
], [.5,2.6,3.0], 'Model-specific connector mapping. [H7,H8,H9]')
p('Kvaser U100-C and Leaf Light R v2 have no built-in CAN termination. On PEAK the two combined CAN/LIN connectors repeat this mapping. '
  'Do not treat PEAK pin 5/6 LIN ground as a generic CAN shield. [H7,H8,H9]')
note('Firmware/backend changes do not change CANable terminal identities. gs_usb is a software interface, not a connector standard. Other candleLight boards require their own silkscreen/manual.')

# 18 --------------------------------------------------------------------------
page('7.3 Vector connector pinouts','vector_pins')
h('VN1610: dual CAN connector before a Y splitter')
table(['Pin','VN1610 CH1/CH2 combined D-Sub'],[
    ['1','CH2 CAN-L'],['2','CH1 CAN-L'],['3','CH1 bus ground'],['4','CH2 CAN-L duplicate shown in the connector/cable map; not LIN'],
    ['5','Shield'],['6','CH2 bus ground'],['7','CH1 CAN-H'],['8','CH2 CAN-H'],['9','No bus signal shown / leave unconnected'],
], [.5,5.6], 'Use the exact revision and Vector CANcable 2Y mapping. CH2 branch maps device 1/6/8 to branch 2/3/7. [H5]')
h('VN1640 / VN1640A dedicated bus sockets')
table(['Pin','High-speed CAN example: 1041Amag','LIN example: 7269mag'],[
    ['1','Reserved / leave open','Reserved / leave open'],['2','CAN-L','Reserved / leave open'],['3','VB- (bus reference)','VB- (LIN reference)'],
    ['4','Split function; not generic CAN-L','Pdis: power-disable control'],['5','Shield','Shield'],['6','Reserved / leave open','Reserved / leave open'],
    ['7','CAN-H','LIN bus'],['8','Reserved / leave open','Reserved / leave open'],['9','VB+ per piggy supply rules','VB+ per piggy supply rules'],
], [.5,2.8,2.8], 'Conditional piggy examples, not an assertion about the user\'s uninspected piggy model. [H6]')
p('For VN1630/VN1630A shared CH1/CH3 or CH2/CH4 sockets: with the documented piggy configuration, secondary built-in CAN uses 1 = CAN-L, 6 = GND, 8 = CAN-H; '
  'the primary uses piggy-defined pins. Without a piggy, documented built-in CAN uses 2 = CAN-L, 3 = GND, 7 = CAN-H. DIP settings and revision determine routing. [H5]')
note('Read the actual transceiver name in Vector Hardware Config/Manager before wiring. Other CAN/LIN piggies can have different power, isolation and auxiliary pins. '
     'For 7269mag, Pdis-to-VB- disables internal supply; apply external supply only according to its manual. CH5 IO and the sync connector are not CAN/LIN bus connectors. [H6]')

# 19 --------------------------------------------------------------------------
page('7.4 Intrepid CAN-family nine-pin maps','intrepid_can_pins')
table(['Pin','ValueCAN3','ValueCAN4-1','ValueCAN4-2'],[
    ['1','Trigger','NC','NC'],['2','CAN1-L','CAN1-L','CAN1-L'],['3','GND','GND','GND'],
    ['4','CAN2-L','NC','CAN2-L'],['5','GND / shield','GND / shield','GND / shield'],['6','GND','GND','GND'],
    ['7','CAN1-H','CAN1-H','CAN1-H'],['8','CAN2-H','NC','CAN2-H'],['9','V+ optional supply; see model guide','Unassigned (-)','Unassigned (-)'],
], [.45,1.9,1.9,1.9], 'Intrepid model-specific D-Sub; ValueCAN3 pin 9 is not equivalent to ValueCAN4-2. [H3,H4]')
h('ValueCAN4-4 with four separate DB9 branches')
p('For the standard four-DB9 branch cable, each labeled CAN branch uses 2 = CAN-L, 3 = GND and 7 = CAN-H. '
  'The molded branch identifies CAN1, CAN2, CAN3 or CAN4. Leave other contacts alone unless the exact cable pinout assigns them. '
  'A DB26 or OBD version is a different harness, not this four-DB9 map. [H11]')
table(['ValueCAN4-4 signal','DB26HD contact(s)','OBD-II contact(s)'],[
    ['CAN1-L / CAN1-H','4 / 14','14 / 6'],['CAN2-L / CAN2-H','6 / 16','11 / 3'],
    ['CAN3-L / CAN3-H','7 / 17','13 / 12'],['CAN4-L / CAN4-H','2 / 12','9 / 1'],
    ['Ground / supply','Not assigned in the cited diagram; verify cable','4 = CHS GND; supply not assigned'],
], [1.7,2.2,2.2], 'ValueCAN4-4 alternative DB26HD [H11D] and OBD-II [H11E] maps. Unlisted contacts have no assignment in those source diagrams.',compact=True)
h('ValueCAN / neoVI is a backend family, not one connector')
p('The GUI accepts Intrepid network names such as HSCAN and HSCAN2 (CAN1 and CAN2 on ValueCAN4-2). It may accept other neoVI hardware through python-can, '
  'but that does not make these tables valid for every neoVI cable. For another model, use the manufacturer\'s serial/model/cable-specific map and verify its capabilities first.')
note('The ValueCAN4-2EL connector is not the ValueCAN4-2 dual-CAN DB9. Its DB26, OBD-II and MULTI variants are on the following pages. '
     'A generic dual-CAN splitter can put a signal on a LIN or power contact when used with the wrong adapter.')
p('Virtual CAN and Virtual LIN have no physical pins. Custom/remapped cables and unspecified piggies require their exact manufacturer map.','Small')

# 20 --------------------------------------------------------------------------
page('7.5 ValueCAN4-2EL: DB26 and OBD-II','el_pins')
p('The two columns below are different cable products, not a one-to-one adapter wiring instruction. Dash means outside the OBD connector pin count. '
  'The vendor tables leave some DB26 contacts blank; those are shown as unassigned. Ethernet is now selectable; use the correct breakout and external 12 V. The app does not control ETH ACTIVATE. [H11]')
db26={1:'ETH TX+',4:'CAN1-L',6:'CAN2-L',9:'ETH TX-',10:'GND',11:'ETH RX+',14:'CAN1-H',16:'CAN2-H',19:'VBAT',20:'ETH RX-',22:'ISO9141/K/LIN',25:'ETH ACTIVATE'}
obd={1:'CAN2-H',2:'NC',3:'ETH TX+',4:'NC',5:'GND',6:'CAN1-H',7:'ISO9141/K/LIN',8:'ETH ACTIVATE',9:'CAN2-L',10:'NC',11:'ETH TX-',12:'ETH RX+',13:'ETH RX-',14:'CAN1-L',15:'NC',16:'VBAT'}
table(['Pin','2EL DB26HD variant','2EL OBD-II variant'],[
    [str(i),db26.get(i,'Unassigned'),obd.get(i,'-')] for i in range(1,27)
], [.5,2.8,2.8], 'ValueCAN4-2ELA/C-DB26 [H11A] and ValueCAN4-2ELA/C-OBD [H11B] connector maps.',compact=True)
note('Standard 2EL LIN supply examples: DB26 pin 19 VBAT with pin 10 GND, LIN on 22; or OBD-II pin 16 VBAT with pin 5 GND, LIN on 7. '
     'Use the vendor-specified 12-V supply and correct cable variant. Do not infer a universal OBD diagnostic wiring policy from these vendor-defined optional pins. [H2,H11]')

# 21 --------------------------------------------------------------------------
page('7.6 ValueCAN4-2EL MULTI harness','el_multi')
p('The MULTI product has distinct DB9, DB25 and OBD-II branches. The DB9 branch carries LIN, not the standard CAN pins. '
  'Blank contacts in the vendor diagram are shown as unassigned. Follow the labeled branch and exact harness part number. [H11]')
db9={1:'ISO9141/K/LIN',5:'GND / SHIELD'}
db25={13:'GND',14:'CAN1-H',15:'CAN1-L',16:'CAN2-H',17:'CAN2-L',25:'VBAT'}
multi_obd={1:'NC',2:'NC',3:'ETH TX+',4:'NC',5:'GND',6:'NC',7:'NC',8:'ETH ACTIVATE',9:'NC',10:'NC',11:'ETH TX-',12:'ETH RX+',13:'ETH RX-',14:'NC',15:'NC',16:'VBAT'}
table(['Pin','MULTI DB9','MULTI DB25','MULTI OBD-II'],[
    [str(i),db9.get(i,'Unassigned') if i<=9 else '-',db25.get(i,'Unassigned'),multi_obd.get(i,'-')] for i in range(1,26)
], [.4,1.8,1.8,2.1], 'VCAN4-2ELA/C-MULTI branch tables [H11C]. Dash = outside that connector\'s pin count.',compact=True)
note('Unlike the dedicated OBD-II 2EL cable, the MULTI OBD-II branch does not carry CAN or LIN on pins 6/7/14 in the published diagram. '
     'Use its DB25 CAN branch and DB9 LIN branch. Check continuity with power disconnected if a custom harness or pin-remapping option is present.')

# 22 --------------------------------------------------------------------------
page('7.7 Ethernet connector reference','ethernet_pins')
h('PC Ethernet: conventional 8P8C / RJ45 interface')
p('Follow the jack/cable manufacturer\'s pin numbering; latch-side and contact-side views are different. The table is the conventional MDI signal assignment, '
  'not a promise about fixed TX/RX direction on auto-MDI-X hardware. Ethernet is not a D-Sub interface. [E3]')
table(['Pin','10/100BASE-T MDI','1000BASE-T pair'],[
    ['1','TX+','TP0+'],['2','TX-','TP0-'],['3','RX+','TP1+'],['4','Not a 10/100 data contact','TP2+'],
    ['5','Not a 10/100 data contact','TP2-'],['6','RX-','TP1-'],['7','Not a 10/100 data contact','TP3+'],['8','Not a 10/100 data contact','TP3-'],
], [.5,2.8,2.8], 'Conventional copper Ethernet assignments. Gigabit uses all four bidirectional pairs; auto-MDI-X can exchange MDI roles. [E3]')
note('Unused-for-data does not mean safe for arbitrary power. PoE and proprietary ports can use these contacts. '
     'Never apply ValueCAN VBAT or LIN supply directly to an Ethernet data pair. PC NIC isolation, PoE capability and link speeds require the exact NIC manual.')
h('ValueCAN4-2EL cable contacts')
table(['Signal','Dedicated DB26HD','Dedicated OBD-II / MULTI OBD-II'],[
    ['ETH TX+ / TX-','1 / 9','3 / 11'],['ETH RX+ / RX-','11 / 20','12 / 13'],
    ['ETH ACTIVATE','25','8; listed for identification, not app-controlled'],
    ['VBAT / ground','19 / 10','16 / 5'],
], [1.7,1.7,2.7], 'Subset of the full ValueCAN connector maps in sections 7.5-7.6; verify the exact harness. [H11A,H11B,H11C]')
p('Use the approved Intrepid Ethernet breakout for the actual DB26/OBD/MULTI cable. These contact numbers identify the vendor cable, '
  'not an instruction to wire a generic OBD connector or RJ45 lead. LIN and CAN pins remain as listed in their model-specific tables.')
p('The ValueCAN4-2EL PHY is conventional 10/100 Ethernet. Automotive 100BASE-T1 requires an appropriate converter, such as the vendor\'s RAD-Moon accessory; '
  'a passive RJ45-to-single-pair cable is not a PHY converter. No native 100BASE-T1 PC adapter backend is supplied. [H2]')

page('7.8 Command line and configuration','cli')
h('Launch commands')
code('cd "C:\\Tools\\Signal Explorer"\n.\\.venv\\Scripts\\python.exe -m signal_explorer')
p('The installed console entry point is also <b>signal-explorer</b> when the virtual environment Scripts folder is on PATH. '
  'SignalExplorer.bat in this project contains a development-PC path; inspect/update it on another PC.')
h('Application arguments: none implemented')
p('There is no application argument parser. Do not use invented options such as --adapter, --channel, --bitrate, --dbc, --log or --replay. '
  'Select those settings in the GUI. QApplication receives sys.argv and may process its own Qt options; those are not Signal Explorer connection features.')
table(['Configuration','Meaning'],[
    ['SIGNAL_EXPLORER_VECTOR_DLL','Absolute path to the installed matching XL DLL, normally vxlapi64.dll. Driver discovery exposes its folder before Vector import. Restart after changes.'],
    ['Vector DLL button / QSettings','The GUI stores drivers/vector_dll in app settings. The environment variable takes priority.'],
    ['SIGNAL_EXPLORER_PLIN_DLL','Absolute path to installed PLinApi.dll when it is not found through normal Windows library loading.'],
    ['QT_QPA_PLATFORM=offscreen','Used for automated headless UI checks, not normal interactive operation. Do not set it in a normal launch session.'],
], [2.2,3.9], 'Supported project environment hooks; vendor runtimes still must be installed.')
h('Developer / verification commands')
code('.\\.venv\\Scripts\\python.exe -m pip check\n.\\.venv\\Scripts\\python.exe -m unittest discover -s tests -v')
p('Automated checks use Virtual buses, simulation and mocked vendor APIs. They do not establish a new adapter\'s physical timing or electrical performance.')
h('Where behavior is defined')
p('<b>ui/main_window.py</b>: CAN controls; <b>ui/lin_panel.py</b>: LIN controls; <b>ui/*trace_model.py</b>: columns, counts and history; '
  '<b>bus/backends.py</b> and <b>bus/canfd.py</b>: rates/backend policy; <b>lin/vector.py</b>, <b>lin/intrepid.py</b>, <b>lin/peak.py</b>: native LIN; '
  '<b>ui/ethernet_panel.py</b> and <b>ethernet/</b>: Ethernet discovery, PC Npcap/Intrepid transports, simulation and raw-frame codec; '
  '<b>recording/</b>: MDF4 capture/replay. All paths are under src/signal_explorer.')
p('Regenerate this guide with docs/build_users_guide.py using Python with reportlab and pypdf. The stored screenshots were captured with simulated traffic only. '
  'Recheck source and vendor revisions after software, hardware, firmware or cable changes.','Small')

# 23 --------------------------------------------------------------------------
page('7.9 Source register','sources')
p('Existing CAN/LIN sources were checked October 25, 2026; Ethernet additions were checked October 26, 2026. '
  'Match the serial, hardware revision and cable/piggy to the cited map. Test-voltage ratings are not interchangeable working-voltage ratings.')
refs=[
 ('H1','CANable product / getting-started pages','https://canable.io/getting-started.html'),
 ('H2','Intrepid ValueCAN4-2EL specifications','https://intrepidcs.com/products/vehicle-network-adapters/valuecan-4/valuecan-4-2el-specifications/'),
 ('H3','Intrepid ValueCAN4 family and model pinouts','https://guide.intrepidcs.com/docs.html'),
 ('H4','Intrepid ValueCAN3 product / pinout','https://store.intrepidcs.com/product/VCAN-DW3'),
 ('H5','Vector-authored VN1600 family manual (vendor download)','https://cdn.vector.com/cms/content/products/VN16xx/docs/VN1600_Interface_Family_Manual_EN.pdf'),
 ('H6','Vector-authored Accessories Manual v5.4, pp. 42-43 and 57-59 (mirror)','https://manualzz.com/doc/11824519/vector-network-interfaces-accessories-user-manual'),
 ('H7','Kvaser U100-C User\'s Guide, pp. 8, 11 and 14','https://pim.kvaser.com/var/assets/Product_Resources/kvaser_u100-C_usersguide.pdf'),
 ('H9','PEAK PCAN-USB Pro FD User Manual, connector and technical sections','https://www.peak-system.com/produktcd/Pdf/English/PCAN-USB-Pro-FD_UserMan_eng.pdf'),
 ('H10','PEAK PLIN API reference','https://www.peak-system.com/produktcd/Develop/PC%20interfaces/Windows/PLIN-API/PLINAPI_enu.pdf'),
 ('H11','Intrepid 2EL / ValueCAN4 connector maps (including MULTI)','https://guide.intrepidcs.com/docs/vcan4-4-2el/Reference-Connector-Pinouts-and-Cable-Signal.html'),
 ('H11A','Intrepid source diagram: 2EL DB26HD pin table','https://guide.intrepidcs.com/docs/vcan4-4-2el/_images/db-26-el.png'),
 ('H11B','Intrepid source diagram: dedicated 2EL OBD-II pin table','https://guide.intrepidcs.com/docs/vcan4-4-2el/_images/obd-connector-el.png'),
 ('H11C','Intrepid source diagram: MULTI DB9 / DB25 / OBD-II tables','https://guide.intrepidcs.com/docs/vcan4-4-2el/_images/fig26.png'),
 ('H11D','Intrepid source diagram: ValueCAN4-4 DB26HD','https://guide.intrepidcs.com/docs/vcan4-4-2el/_images/db-26.png'),
 ('H11E','Intrepid source diagram: ValueCAN4-4 OBD-II','https://guide.intrepidcs.com/docs/vcan4-4-2el/_images/db-16.png'),
 ('F1','Kvaser CAN error handling','https://kvaser.com/lesson/can-error-handling/'),
 ('E1','Npcap installation and user guide','https://npcap.com/guide/npcap-users-guide.html'),
 ('E2','Npcap API reference: capture and packet injection','https://npcap.com/guide/npcap-api.html'),
 ('E3','Oracle Ethernet connector pinouts, sections E.3-E.4','https://docs.oracle.com/cd/E19121-01/sf.x4100m2/819-1157-23/E_Pinouts.html'),
]
for key,label,url in refs:
    p(f'<b>[{key}]</b> <link href="{escape(url)}" color="#124b6a"><u>{escape(label)}</u></link>','Small')
p('<b>[H8]</b> User-supplied Kvaser Leaf Light R v2 User\'s Guide, revision 3.10, printed October 7, 2026, pp. 9-11; '
  '<i>kvaser_leaf_light_r_v2_usersguide_3_10_261007.pdf</i>. This is also the formatting reference. Its specification table gives galvanic isolation without a numerical voltage rating.','Small')
p('Vector\'s official CDN rejected the local download during preparation. Vector-authored manual text was checked via an indexed manual mirror; '
  'for current revision details, use the official manual/Hardware Manager before wiring. Conditional piggy tables are examples and do not identify the user\'s two uninspected LIN piggies.','Small')
page('7.10 Revision history and validation','revision')
table(['Revision / date','Change'],[
    ['1.0 / October 25, 2026','Initial CAN/FD/LIN guide, workflows, fault recovery, adapter pinouts and CLI.'],
    ['1.1 / October 26, 2026','Ethernet simulation, PC Npcap and ValueCAN4-2EL: installation, discovery, GUI map, sending/filtering, MF4/replay, diagnostics, connectors and validation scope.'],
    ['1.2 / October 29, 2026','Python editor/runner and CAN API; continuous 321/221 demo; File Viewer and read-only timed MF4 replay; SE icon/splash; Intrepid clock alignment and replay discontinuity/timeout fixes. Companion Scripting User\'s Guide and nine source examples added.'],
], [1.7,4.4], 'Document revision, independent of the application version.')
p('Signal Explorer is the application name used here. Vendor names identify supported interfaces and source documents; this guide does not imply vendor endorsement, product certification or independent ASAM conformance certification.','Small')
h('Validation for this edition')
p('145 automated tests cover the existing protocols, editor controls, pause/stop, replay direction/timing, log loading, timestamp alignment and the documented script examples. '
  'Five examples run against built-in virtual traffic; four MCU templates run against a private mock firmware peer after enabling their contract flag in the test harness. '
  'The default MCU templates are also checked to reject transmission before configuration. Native electrical qualification remains outside these tests.')
p('The source code, docs/scripting.md, docs/file-viewer.md, examples/scripts/mcu/ and tests/test_scripting_examples.py define the October 29 behavior. '
  'Guide generators and capture scripts are in docs/. Dates above distinguish software/document updates from earlier hardware-source checks.')

class ManualDoc(SimpleDocTemplate):
    def afterFlowable(self,flowable):
        if hasattr(flowable,'anchor'):
            self.canv.bookmarkPage(flowable.anchor)
            self.canv.addOutlineEntry(flowable.getPlainText(),flowable.anchor,0,False)

def decorate(canvas,doc):
    canvas.saveState()
    canvas.setStrokeColor(colors.black);canvas.setLineWidth(.5)
    if doc.page>1:
        canvas.setFont('Helvetica-Bold',9)
        canvas.drawString(52,H-36,"Signal Explorer User's Guide")
        canvas.setFont('Helvetica',9);canvas.drawRightString(W-52,H-36,f'{doc.page} ({len(pages)})')
        canvas.line(52,H-41,W-52,H-41)
    canvas.line(52,40,W-52,40)
    canvas.setFont('Helvetica',8)
    canvas.drawString(52,26,'SIGNAL EXPLORER  |  Revision 1.2  |  October 29, 2026')
    canvas.drawRightString(W-52,26,str(doc.page))
    canvas.restoreState()

if __name__=='__main__':
    numbering={anchor:i for i,(_,anchor) in enumerate(pages,1)}
    for paragraph,first,last in page_refs:
        start=numbering[first];end=numbering[last] if last else start
        paragraph.__init__(str(start) if start==end else f'{start}-{end}',styles['Cell'])
    doc=ManualDoc(str(OUT),pagesize=A4,leftMargin=52,rightMargin=52,topMargin=61,bottomMargin=54,
                  title="Signal Explorer User's Guide",author='Signal Explorer')
    doc.build(story,onFirstPage=decorate,onLaterPages=decorate)
    reader=PdfReader(str(OUT))
    print(f'Created {OUT}; {len(reader.pages)} pages; expected {len(pages)}.')
    for i,(pdf_page,(title,_)) in enumerate(zip(reader.pages,pages),1):
        text=pdf_page.extract_text()
        print(f'{i:02} {len(text):4} {title}')
        assert title.replace('&','&') in text.replace('\n',' '),f'Unexpected pagination on page {i}: {title}'
    assert len(reader.pages)==len(pages),'Unexpected overflow page: inspect and adjust layout.'
    assert len(reader.pages)==35
