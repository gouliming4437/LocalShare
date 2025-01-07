# Standard library imports
import os
import json
import logging
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
import qrcode
import base64
from io import BytesIO
import socket
from contextlib import closing
import threading
from flask import current_app

# Third-party imports
from flask import Flask, render_template, request, send_file, jsonify
from flask_socketio import SocketIO, emit
import netifaces
from werkzeug.utils import secure_filename

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Flask application setup
app = Flask(__name__)
app.config.update(
    SECRET_KEY='your-secret-key-here',
    MAX_CONTENT_LENGTH=1024 * 1024 * 1024,  # 1GB max file size
    UPLOAD_FOLDER=tempfile.mkdtemp()  # Temporary folder for file transfers
)

# Socket.IO setup
socketio = SocketIO(app, cors_allowed_origins="*", ping_timeout=60)

# Global state
connected_devices = {}  # Store connected devices with additional metadata
active_transfers = {}   # Store ongoing transfers
server_port = None  # Global variable to store the port

# Utility functions
def get_ip_addresses():
    """Get all non-loopback IPv4 addresses for this machine."""
    ip_addresses = []
    interfaces = netifaces.interfaces()
    
    for interface in interfaces:
        addrs = netifaces.ifaddresses(interface)
        if netifaces.AF_INET in addrs:
            for addr in addrs[netifaces.AF_INET]:
                ip_addresses.append(addr['addr'])
    
    return [ip for ip in ip_addresses if not ip.startswith('127.')]

def get_device_ip(request):
    """Get the actual IP address of a connecting device."""
    if request.environ.get('HTTP_X_FORWARDED_FOR'):
        device_ip = request.environ['HTTP_X_FORWARDED_FOR'].split(',')[0]
    else:
        device_ip = request.environ.get('REMOTE_ADDR')
    
    # If it's localhost/127.0.0.1, get the actual network IP
    if device_ip in ['127.0.0.1', 'localhost']:
        network_ips = get_ip_addresses()
        if network_ips:
            device_ip = network_ips[0]  # Use the first non-loopback IP
    
    return device_ip

def cleanup_old_transfers():
    """Remove old transfer data and files."""
    # TODO: Implement periodic cleanup of old transfers
    pass

def generate_qr_code(url):
    """Generate QR code for the given URL and return as base64 string."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode()

def find_available_port(start_port=5000, max_port=5050):
    """Find first available port in range [start_port, max_port]."""
    global server_port
    if server_port:  # If port is already found, return it
        return server_port
        
    for port in range(start_port, max_port + 1):
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            try:
                sock.bind(('', port))
                logger.info(f"Found available port: {port}")
                server_port = port  # Store the found port
                return port
            except socket.error:
                logger.debug(f"Port {port} is in use, trying next port")
                continue
    raise RuntimeError(f"Could not find an open port between {start_port} and {max_port}")

@app.route('/')
def index():
    ip_addresses = get_ip_addresses()
    if ip_addresses:
        # Get port from environment variable since it's set during startup
        port = os.environ.get('SERVER_PORT', '5000')
        url = f"http://{ip_addresses[0]}:{port}"
        qr_code = generate_qr_code(url)
        return render_template('index.html', qr_code=qr_code, server_url=url)
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    try:
        logger.info(f'Received upload request. Files: {request.files}, Form: {request.form}')
        logger.info(f'Current active transfers: {json.dumps(active_transfers)}')
        
        if 'file' not in request.files:
            logger.error('No file in request')
            return jsonify({'error': 'No file part'}), 400
        
        file = request.files['file']
        transfer_id = request.form.get('transfer_id')
        relative_path = request.form.get('relative_path', '')
        is_directory = request.form.get('is_directory') == 'true'
        
        if not transfer_id:
            logger.error('No transfer_id in upload request')
            return jsonify({'error': 'No transfer ID'}), 400
            
        if transfer_id not in active_transfers:
            logger.error(f'Transfer {transfer_id} not found. Active transfers: {list(active_transfers.keys())}')
            return jsonify({'error': 'Invalid transfer ID'}), 400
            
        transfer = active_transfers[transfer_id]
        logger.info(f'Found transfer data: {json.dumps(transfer)}')
        
        if transfer['status'] != 'accepted':
            logger.error(f'Invalid transfer status for upload: {transfer["status"]}')
            return jsonify({'error': f'Invalid transfer status: {transfer["status"]}'}), 400
        
        if file.filename == '':
            logger.error('No selected file')
            return jsonify({'error': 'No selected file'}), 400
        
        # Create transfer directory and save file
        transfer_path = Path(app.config['UPLOAD_FOLDER']) / transfer_id
        
        if is_directory:
            # For directory uploads, maintain the directory structure
            file_path = transfer_path / relative_path
        else:
            # For single files, just use the filename
            file_path = transfer_path / secure_filename(file.filename)
            
        # Create all parent directories
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        logger.info(f'Saving file to {file_path}')
        file.save(file_path)
        
        # Update transfer status and file info
        active_transfers[transfer_id]['uploaded_files'] += 1
        active_transfers[transfer_id]['files'].append({
            'path': str(file_path),
            'relative_path': relative_path,
            'size': os.path.getsize(file_path)
        })
        
        logger.info(f'Uploaded {active_transfers[transfer_id]["uploaded_files"]} of {active_transfers[transfer_id]["total_files"]} files')
        
        # If this is the last file, mark as ready for download
        if active_transfers[transfer_id]['uploaded_files'] >= active_transfers[transfer_id]['total_files']:
            active_transfers[transfer_id].update({
                'status': 'ready_for_download',
                'base_path': str(transfer_path),
                'uploaded_at': datetime.now().isoformat()
            })
            logger.info(f'All files uploaded. Updated transfer status: {json.dumps(active_transfers[transfer_id])}')
            
            # Notify recipient
            recipient_sid = transfer['recipient_sid']
            logger.info(f'Notifying recipient {recipient_sid} about ready files')
            
            socketio.emit('file_ready_for_download', {
                'transfer_id': transfer_id,
                'filename': os.path.basename(relative_path) if is_directory else file.filename,
                'download_url': f'/download/{transfer_id}',
                'is_directory': is_directory
            }, room=recipient_sid)
        
        return jsonify({
            'success': True,
            'transfer_id': transfer_id,
            'message': 'File uploaded successfully'
        })
        
    except Exception as e:
        logger.error(f'Upload error: {str(e)}', exc_info=True)
        return jsonify({'error': str(e)}), 500

@app.route('/download/<transfer_id>')
def download_file(transfer_id):
    try:
        logger.info(f'Download requested for transfer ID: {transfer_id}')
        
        if not transfer_id:
            logger.error('No transfer ID provided')
            return jsonify({'error': 'No transfer ID provided'}), 400
            
        if transfer_id not in active_transfers:
            logger.error(f'Invalid transfer ID: {transfer_id}. Active transfers: {list(active_transfers.keys())}')
            return jsonify({'error': 'Invalid transfer ID'}), 404
        
        transfer = active_transfers[transfer_id]
        logger.info(f'Found transfer: {json.dumps(transfer)}')
        
        # Verify transfer status
        if transfer['status'] != 'ready_for_download':
            logger.error(f'Files not ready for download. Current status: {transfer["status"]}')
            return jsonify({
                'error': 'Files not ready for download',
                'status': transfer['status']
            }), 400
        
        base_path = transfer['base_path']
        if not os.path.exists(base_path):
            logger.error(f'Base path not found: {base_path}')
            return jsonify({'error': 'Files not found'}), 404

        # Check if request is from a mobile device
        user_agent = request.headers.get('User-Agent', '').lower()
        is_mobile = 'mobile' in user_agent or 'iphone' in user_agent or 'android' in user_agent
        logger.info(f'User agent: {user_agent}, Is mobile: {is_mobile}')

        if transfer.get('is_directory', False) and is_mobile:
            try:
                # For mobile devices, always create a zip file
                import zipfile
                import io
                
                # Create zip file in memory
                memory_file = io.BytesIO()
                with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for file_info in transfer['files']:
                        src_path = file_info['path']
                        rel_path = file_info['relative_path']
                        
                        # Normalize the relative path
                        rel_path = os.path.normpath(rel_path)
                        
                        if os.path.exists(src_path):
                            logger.info(f'Adding to zip: {src_path} as {rel_path}')
                            zf.write(src_path, rel_path)
                
                memory_file.seek(0)
                dir_name = os.path.basename(transfer['filename'])
                return send_file(
                    memory_file,
                    mimetype='application/zip',
                    as_attachment=True,
                    download_name=f'{dir_name}.zip'
                )
                
            except Exception as e:
                logger.error(f'Error creating zip file: {str(e)}', exc_info=True)
                return jsonify({
                    'error': f'Error creating zip file: {str(e)}'
                }), 500
        elif transfer.get('is_directory', False):
            # Desktop handling remains the same
            try:
                # Get the download directory from query parameters
                download_dir = request.args.get('download_dir', '')
                logger.info(f'Download directory requested: {download_dir}')

                # Determine the target directory
                if download_dir:
                    # Use specified download directory
                    target_base = os.path.abspath(download_dir)
                else:
                    # Use system-specific default downloads directory
                    if os.name == 'nt':  # Windows
                        target_base = os.path.join(os.path.expanduser('~'), 'Downloads')
                    else:  # macOS and Linux
                        target_base = os.path.join(str(Path.home()), 'Downloads')
                
                logger.info(f'Using target directory: {target_base}')
                
                # Create the target directory if it doesn't exist
                os.makedirs(target_base, exist_ok=True)
                
                # Get the directory name from the transfer
                dir_name = os.path.basename(transfer['filename'])
                target_dir = os.path.join(target_base, dir_name)
                
                # Create a unique directory name if it already exists
                counter = 1
                original_target_dir = target_dir
                while os.path.exists(target_dir):
                    target_dir = f"{original_target_dir}_{counter}"
                    counter += 1
                
                logger.info(f'Creating directory: {target_dir}')
                os.makedirs(target_dir, exist_ok=True)
                
                # Copy all files maintaining directory structure
                files_copied = []
                for file_info in transfer['files']:
                    try:
                        src_path = file_info['path']
                        rel_path = file_info['relative_path']
                        
                        # Normalize the relative path based on OS
                        rel_path = os.path.normpath(rel_path)
                        if os.name == 'nt':  # Windows
                            rel_path = rel_path.replace('/', '\\')
                        else:  # Unix-like
                            rel_path = rel_path.replace('\\', '/')
                        
                        # Calculate the target path relative to the new directory
                        rel_parts = rel_path.split(os.sep)
                        if len(rel_parts) > 1:
                            # Skip the first component (original dir name)
                            rel_path = os.path.join(*rel_parts[1:])
                        target_path = os.path.join(target_dir, rel_path)
                        
                        # Create parent directories
                        os.makedirs(os.path.dirname(target_path), exist_ok=True)
                        
                        # Copy the file with error handling
                        logger.info(f'Copying file: {src_path} -> {target_path}')
                        if os.path.exists(src_path):
                            shutil.copy2(src_path, target_path)
                            files_copied.append(target_path)
                        else:
                            logger.error(f'Source file not found: {src_path}')
                    except Exception as e:
                        logger.error(f'Error copying file {src_path}: {str(e)}')
                        continue
                
                if not files_copied:
                    raise Exception('No files were copied successfully')
                
                logger.info(f'Successfully copied {len(files_copied)} files to {target_dir}')
                return jsonify({
                    'success': True,
                    'message': 'Directory downloaded successfully',
                    'path': target_dir,
                    'files_copied': len(files_copied)
                })
                
            except Exception as e:
                logger.error(f'Error copying files: {str(e)}', exc_info=True)
                # Clean up the target directory if it exists and is empty
                try:
                    if 'target_dir' in locals() and os.path.exists(target_dir):
                        if not os.listdir(target_dir):
                            os.rmdir(target_dir)
                except:
                    pass
                return jsonify({
                    'error': f'Error copying files: {str(e)}',
                    'details': {
                        'target_dir': target_dir if 'target_dir' in locals() else None,
                        'files_copied': len(files_copied) if 'files_copied' in locals() else 0
                    }
                }), 500
        else:
            # Single file download
            file_path = transfer['files'][0]['path']
            return send_file(
                file_path,
                as_attachment=True,
                download_name=os.path.basename(file_path)
            )
        
    except Exception as e:
        logger.error(f'Download error: {str(e)}', exc_info=True)
        return jsonify({'error': str(e)}), 500

@socketio.on('connect')
def handle_connect():
    device_id = request.sid
    device_name = request.args.get('device_name', f'Device_{device_id[:6]}')
    device_ip = get_device_ip(request)
    
    # Remove any existing connections with the same IP
    disconnected_ids = []
    for sid, device in connected_devices.items():
        if device['ip'] == device_ip and sid != device_id:
            disconnected_ids.append(sid)
    
    for sid in disconnected_ids:
        del connected_devices[sid]
    
    logger.info(f'Device connected: {device_name} ({device_id}) from IP: {device_ip}')
    
    connected_devices[device_id] = {
        'id': device_id,
        'name': device_name,
        'ip': device_ip
    }
    
    emit('device_list', connected_devices, broadcast=True)

@socketio.on('disconnect')
def handle_disconnect():
    device_id = request.sid
    if device_id in connected_devices:
        logger.info(f'Device disconnected: {connected_devices[device_id]["name"]} ({device_id})')
        del connected_devices[device_id]
        emit('device_list', connected_devices, broadcast=True)

@socketio.on('file_transfer_request')
def handle_file_transfer_request(data):
    try:
        logger.info(f'Received file transfer request: {json.dumps(data)}')
        if 'target' not in data:
            logger.error('No target specified in file transfer request')
            return
            
        if data['target'] not in connected_devices:
            logger.error(f'Target device {data["target"]} not found in connected devices')
            emit('file_transfer_error', {
                'error': 'Target device not found'
            }, room=request.sid)
            return
            
        transfer_id = os.urandom(16).hex()
        active_transfers[transfer_id] = {
            'sender': request.sid,
            'recipient_sid': data['target'],
            'filename': data.get('filename', 'unknown'),
            'filesize': data.get('filesize', 0),
            'status': 'pending',
            'is_directory': data.get('isDirectory', False),
            'total_files': data.get('total_files', 1),
            'uploaded_files': 0,
            'files': [],
            'created_at': datetime.now().isoformat()
        }
        
        logger.info(f'Created transfer {transfer_id} from {connected_devices[request.sid]["name"]} to {connected_devices[data["target"]]["name"]}')
        logger.info(f'Transfer data: {json.dumps(active_transfers[transfer_id])}')
        
        # Notify recipient about the transfer request
        transfer_request = {
            'transfer_id': transfer_id,
            'from': request.sid,
            'from_name': connected_devices[request.sid]['name'],
            'filename': data.get('filename', 'unknown'),
            'filesize': data.get('filesize', 0),
            'is_directory': data.get('isDirectory', False)
        }
        logger.info(f'Sending transfer request to recipient: {json.dumps(transfer_request)}')
        emit('file_transfer_request', transfer_request, room=data['target'])
        
        # Also notify sender that request was sent
        emit('file_transfer_request_sent', {
            'transfer_id': transfer_id,
            'target': data['target']
        }, room=request.sid)
        
    except Exception as e:
        logger.error(f'Error in file transfer request: {str(e)}', exc_info=True)
        emit('file_transfer_error', {
            'error': 'Internal server error during transfer request'
        }, room=request.sid)

@socketio.on('file_transfer_accept')
def handle_file_transfer_accept(data):
    try:
        logger.info(f'Received transfer accept: {json.dumps(data)}')
        transfer_id = data.get('transfer_id')
        
        if not transfer_id:
            logger.error('No transfer_id in accept request')
            return
            
        if transfer_id not in active_transfers:
            logger.error(f'Transfer {transfer_id} not found in active transfers')
            emit('file_transfer_error', {
                'error': 'Transfer not found'
            }, room=request.sid)
            return
            
        transfer = active_transfers[transfer_id]
        if transfer['sender'] not in connected_devices:
            logger.error(f'Sender {transfer["sender"]} not connected')
            emit('file_transfer_error', {
                'error': 'Sender disconnected'
            }, room=request.sid)
            return
            
        # Don't override the recipient_sid that was set during request
        active_transfers[transfer_id].update({
            'status': 'accepted',
            'accepted_at': datetime.now().isoformat()
        })
        logger.info(f'Transfer {transfer_id} accepted by {connected_devices[request.sid]["name"]}')
        logger.info(f'Updated transfer data: {json.dumps(active_transfers[transfer_id])}')
        
        # Send upload URL to sender
        emit('file_transfer_accepted', {
            'transfer_id': transfer_id,
            'upload_url': f'/upload',
            'recipient_name': connected_devices[request.sid]['name']
        }, room=transfer['sender'])
        
    except Exception as e:
        logger.error(f'Error in file transfer accept: {str(e)}', exc_info=True)
        emit('file_transfer_error', {
            'error': 'Internal server error during transfer accept'
        }, room=request.sid)

@socketio.on('file_transfer_reject')
def handle_file_transfer_reject(data):
    try:
        transfer_id = data.get('transfer_id')
        if not transfer_id:
            logger.error('No transfer_id in reject request')
            return
            
        if transfer_id not in active_transfers:
            logger.error(f'Transfer {transfer_id} not found in active transfers')
            return
            
        transfer = active_transfers[transfer_id]
        if transfer['sender'] in connected_devices:
            logger.info(f'File transfer rejected by {connected_devices[request.sid]["name"]}')
            emit('file_transfer_rejected', {
                'transfer_id': transfer_id
            }, room=transfer['sender'])
            
        # Clean up the transfer
        del active_transfers[transfer_id]
        logger.info(f'Transfer {transfer_id} cleaned up after rejection')
        
    except Exception as e:
        logger.error(f'Error in file transfer reject: {str(e)}', exc_info=True)

@socketio.on('file_transfer_complete')
def handle_file_transfer_complete(data):
    transfer_id = data['transfer_id']
    if transfer_id in active_transfers:
        # Clean up the transfer
        transfer_path = Path(app.config['UPLOAD_FOLDER']) / transfer_id
        if transfer_path.exists():
            shutil.rmtree(transfer_path)
        del active_transfers[transfer_id]
        logger.info(f'File transfer completed and cleaned up: {transfer_id}')

if __name__ == '__main__':
    try:
        # Only find port in the main process
        if not os.environ.get('WERKZEUG_RUN_MAIN'):
            port = find_available_port()
            os.environ['SERVER_PORT'] = str(port)
        else:
            port = int(os.environ.get('SERVER_PORT'))
            
        if not os.environ.get('WERKZEUG_RUN_MAIN'):
            logger.info(f"Starting server on port {port}")
            print(f"\n* Server is running on port {port}")
            print(f"* Access URLs:")
            for ip in get_ip_addresses():
                print(f"*   http://{ip}:{port}")
            print("\n* Press Ctrl+C to quit\n")
            
        # Set the port in app config so it's accessible everywhere
        app.config['SERVER_PORT'] = port
        socketio.run(app, host='0.0.0.0', port=port, debug=True)
    except Exception as e:
        logger.error(f"Failed to start server: {str(e)}")
        print(f"\nError: {str(e)}") 