# LocalShare

A simple and efficient local network file sharing application that allows devices to transfer files and directories directly over your local network.

## Features

- **Easy Device Discovery**: Automatically discovers devices on your local network
- **Real-Time Updates**: Shows connected devices and their IP addresses in real-time
- **File & Directory Transfer**: 
  - Send individual files or entire directories
  - Maintains directory structure during transfer
  - Shows transfer progress
- **Cross-Platform Support**:
  - Works on Windows, macOS, and Linux
  - Mobile support with automatic ZIP compression for directories
  - QR code for quick connection
- **Custom Download Location**: Choose where to save your received files
- **User-Friendly Interface**: Clean, modern web interface

## Requirements

- Python 3.7+
- Flask
- Flask-SocketIO
- netifaces

## Installation

1. Clone the repository:
```bash
git clone https://github.com/gouliming4437/LocalShare.git
cd LocalShare
```

2. Install dependencies:
```bash
pip install flask flask-socketio netifaces
```

3. Run the application:
```bash
python app.py
```

4. Open in browser:
- This application will automatically scan available ports and serve the web interface on the first available port.

## Usage

1. **Start the Server**:
   - Run the application on any device that will act as the server
   - Note the IP address shown in the console

2. **Connect Devices**:
   - Open the web interface in a browser
   - Enter a device name (optional)
   - You'll see other connected devices automatically
   - You can also scan QR code to connect to the server

3. **Send Files**:
   - Select the recipient device
   - Choose between file or directory upload
   - Drag & drop or click to select files
   - Confirm the transfer on the receiving device

4. **Receive Files**:
   - Accept incoming transfer requests
   - Files are saved to Downloads folder by default
   - Optionally choose a custom save location
   - Directories are automatically extracted on desktop or downloaded as ZIP on mobile

## Security Note

LocalShare is designed for use on trusted local networks only. It does not include encryption for file transfers, so please use it only on secure networks.

## Technical Details

- Built with Flask and Flask-SocketIO for real-time communication
- Uses WebSocket for device discovery and transfer coordination
- HTTP for actual file transfers
- Tailwind CSS for responsive UI
- Handles large files and maintains directory structures
- Automatic cleanup of temporary files after transfer

## Contributing

Feel free to submit issues, fork the repository, and create pull requests for any improvements.

## License

This project is licensed under the MIT License - see the LICENSE file for details. 