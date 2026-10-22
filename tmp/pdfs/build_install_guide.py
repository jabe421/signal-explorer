from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Preformatted, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from pypdf import PdfReader

root = Path.cwd()
output = root / 'Install guide.pdf'
styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name='TitleSE', fontName='Helvetica-Bold', fontSize=26, leading=29, textColor=colors.HexColor('#123148'), spaceAfter=12))
styles.add(ParagraphStyle(name='SubSE', fontName='Helvetica', fontSize=12, leading=17, textColor=colors.HexColor('#446170'), spaceAfter=12))
styles.add(ParagraphStyle(name='HSE', fontName='Helvetica-Bold', fontSize=12, leading=15, textColor=colors.HexColor('#126C79'), spaceBefore=9, spaceAfter=5))
styles.add(ParagraphStyle(name='BodySE', fontName='Helvetica', fontSize=9.5, leading=12.5, spaceAfter=6))
styles.add(ParagraphStyle(name='SmallSE', fontName='Helvetica', fontSize=8.5, leading=11, textColor=colors.HexColor('#446170'), spaceAfter=7))
styles.add(ParagraphStyle(name='CodeSE', fontName='Courier', fontSize=8.5, leading=11, backColor=colors.HexColor('#F0F4F6'), borderPadding=7, spaceBefore=4, spaceAfter=9))
story=[]
def p(text,style='BodySE'):
    story.append(Paragraph(text,styles[style]))
def h(text): p(text,'HSE')
def code(text): story.append(Preformatted(escape(text),styles['CodeSE']))
def page(title,subtitle):
    p(title,'TitleSE'); p(subtitle,'SubSE')
def link(label,url):
    p(f'<link href="{url}" color="#126C79"><u>{label}</u></link>','SmallSE')

page('Install guide','Signal Explorer | Windows source installation<br/>Version 0.1.0 - prepared October 22, 2026')
p('This guide installs the current Python desktop application on another PC. A standalone installer will be addressed later. You need internet access for the initial Python package downloads and any required driver downloads.')
h('1. Prepare the PC and project files')
p('Install <b>64-bit Python 3.12</b> and its Python launcher. This is the version used on the development PC. The project declares Python 3.11 or later, but other versions have not been validated here.')
p('Copy or clone the project into a writable folder, for example <b>C:\\Tools\\Signal Explorer</b>. Include <b>pyproject.toml</b>, <b>README.md</b>, and the complete <b>src</b> folder. Copy <b>examples</b> for the demo DBC and <b>tests</b> if you want to run checks. Do not copy the old <b>.venv</b>; create one on the new PC. Git is optional if you copy the files.')
h('2. Install the application')
p('Open PowerShell in the project root - the folder containing pyproject.toml - and run:')
code('cd "C:\\Tools\\Signal Explorer"\npy -3.12 -m venv .venv\n.\\.venv\\Scripts\\python.exe -m pip install .')
p('Using the virtual environment executable directly avoids the need to activate it or change PowerShell script-execution settings.')
h('3. Launch Signal Explorer')
code('.\\.venv\\Scripts\\python.exe -m signal_explorer')
p('Run this command from the project root whenever you want to start the application. The existing SignalExplorer.bat contains the development PC\'s absolute path; update that path before using it elsewhere.','SmallSE')
h('Packages installed automatically')
p('<b>PySide6-Essentials</b> - desktop UI; <b>python-can</b> - CAN interfaces; <b>pyserial</b> - serial adapters; <b>cantools</b> - DBC decoding; <b>uptime</b> - timestamp support. pip also installs their required dependencies. Build tooling includes setuptools. These are application dependencies, separate from vendor hardware drivers.')

story.append(PageBreak())
page('Adapter requirements','Install only the software needed for the adapters you plan to use.')
p('Use vendor-supplied drivers and APIs matching this 64-bit installation. Driver installers may require Windows administrator approval. Vendor DLLs are not included in Signal Explorer\'s source files.')
h('Virtual CAN - no hardware')
p('No vendor software is required. Choose Virtual and enable Inject demo traffic to test the application without an adapter.')
h('CANable - SLCAN firmware')
p('Windows must recognize the adapter as a serial COM port. Install its appropriate USB serial driver if Windows does not configure it automatically. No additional Python packages are required. Choose CANable (SLCAN serial), select its actual COM port, and match the CAN bus bitrate. The new PC may assign a port other than COM3.')
h('CANable - candleLight / gs_usb firmware')
p('This is a different USB connection path from SLCAN. Install the additional backend:')
code('.\\.venv\\Scripts\\python.exe -m pip install "python-can[gs-usb]"')
p('A compatible USB driver and libusb backend may also be needed on Windows. Configure those for the device\'s firmware, then choose CANable / candleLight (gs_usb). This path has not been physically validated in this project; the tested CANable connection is SLCAN.')
h('Vector - VN16xx / CANXL adapter option')
p('Install Vector hardware drivers <b>and the 64-bit XL Driver Library</b>. The USB driver alone is insufficient. Signal Explorer checks PATH and the standard Public Documents/Vector/XL Driver Library*/bin folder. If detection fails, use <b>Vector DLL</b> to select the installed <b>vxlapi64.dll</b>, then restart. Select the detected serial number and physical CAN channel; no application assignment in Vector Hardware Config is required.')
link('Vector XL Driver Library', 'https://www.vector.com/en/business-unit/testing-validation-diagnostics/products/network-interfaces/xl-driver-library/')
h('Kvaser - including U100-C')
p('Install Kvaser Windows drivers with the CANlib runtime. Select Kvaser (U100 / CANlib), then Refresh devices. Channel indices start at zero. A listed Kvaser virtual channel is not a physical U100-C.')
h('PEAK - including PCAN-USB Pro FD')
p('Install PEAK hardware drivers and the 64-bit <b>PCAN-Basic API</b> (PCANBasic.dll). Select the PEAK option and a detected CAN channel. Manual names include PCAN_USBBUS1 and PCAN_USBBUS2; use detected names when several adapters are attached.')
h('Intrepid - ValueCAN / neoVI')
p('Install Intrepid hardware drivers and its native 64-bit <b>icsneo40.dll</b>, then install the optional Python dependencies:')
code('.\\.venv\\Scripts\\python.exe -m pip install ".[intrepid]"')
p('This adds python-ics and filelock. Select the Intrepid adapter by serial number, not its COM port. For ValueCAN4-2, HSCAN is CAN1 and HSCAN2 is CAN2.')

story.append(PageBreak())
page('Verify and troubleshoot','A short check before connecting to a live CAN bus.')
h('1. Verify the software without hardware')
p('Launch Signal Explorer, select <b>Virtual</b>, enable <b>Inject demo traffic</b>, and click <b>Connect</b>. The trace should show ID 100 with changing data. Load <b>examples/demo.dbc</b> to display the demo message name and decoded signals.')
p('Auto-scroll (all messages) is checked by default and shows timestamped history. Uncheck it for the latest data per channel and ID. History retains the most recent 10,000 frames. <b>Pause Exploring</b> freezes trace updates while staying connected; <b>Start Exploring</b> resumes. Frames received while paused are not replayed.')
h('2. Verify hardware reception')
p('Connect the adapter by USB, choose its backend and channel, set the correct bus bitrate, and click Connect. Use a powered, correctly wired and terminated CAN bus with a device that is transmitting. Check the RX count. Click Clear filter if frames might be hidden. Merely connecting USB does not generate CAN traffic.')
h('Common issues')
p('<b>Python or py is not recognized:</b> verify Python 3.12 is installed and reopen PowerShell. Check with <b>py -3.12 --version</b>.')
p('<b>No module named signal_explorer:</b> run the install command from the project root and launch using the same .venv Python executable.')
p('<b>COM port access denied:</b> disconnect that port in other Signal Explorer windows and close other programs using it. Only one process can hold the CANable serial port.')
p('<b>Connected, RX stays at zero:</b> confirm the bus has traffic, the bitrate matches, CAN-H/CAN-L wiring is correct, and the required termination is present. A quiet bus can connect successfully without producing trace rows.')
p('<b>RX increases but the trace appears empty:</b> click Clear filter and ensure exploring is running. While paused, trace counters and rows stay frozen.')
p('<b>Missing DLL or no detected channels:</b> install the vendor\'s 64-bit API as well as its USB driver. Reconnect USB, use Refresh devices, or restart after changing DLL settings. No attached device can legitimately produce an empty channel list.')
h('Optional automated checks')
p('If the tests folder was copied, run the following from the project root. The complete suite also expects the Intrepid optional dependencies to be installed.')
code('.\\.venv\\Scripts\\python.exe -m unittest discover -s tests -v')
p('Tests use simulated or virtual traffic and do not establish that a new physical adapter is wired and communicating correctly.','SmallSE')
h('Current scope')
p('The app supports <b>Classic CAN and CAN FD, one channel at a time</b>. Enable CAN FD and set arbitration/data bitrates before connecting. Virtual FD demo traffic includes Engine RPM, MAF and O2 signals; load examples/demo.dbc to decode them. Physical FD operation still needs hardware validation. CAN XL, LIN, Ethernet and SENT are not yet exposed. The Vector dropdown\'s CANXL label does not enable CAN XL frames.')
p('DBC files are optional; copy the files you intend to use separately. No compiler is normally needed when compatible prebuilt Python wheels are available. Record working package versions for reproducible deployments; the current project uses minimum version constraints rather than a pinned dependency lock.','SmallSE')

def decorate(canvas,doc):
    w,h=letter
    canvas.setStrokeColor(colors.HexColor('#126C79'))
    canvas.setLineWidth(2)
    canvas.line(43,h-29,w-43,h-29)
    canvas.setFont('Helvetica',8)
    canvas.setFillColor(colors.HexColor('#446170'))
    canvas.drawString(43,25,'SIGNAL EXPLORER  /  INSTALL GUIDE')
    canvas.drawRightString(w-43,25,str(doc.page))

doc=SimpleDocTemplate(str(output),pagesize=letter,rightMargin=43,leftMargin=43,topMargin=46,bottomMargin=43,title='Install guide - Signal Explorer',author='Signal Explorer')
doc.build(story,onFirstPage=decorate,onLaterPages=decorate)
r=PdfReader(str(output))
print('Created:',output)
print('Pages:',len(r.pages))
for i,page in enumerate(r.pages,1):
    text=page.extract_text()
    print(i, len(text),text[:60].replace('\n',' '))
assert len(r.pages)==3, 'Unexpected pagination; inspect layout'
