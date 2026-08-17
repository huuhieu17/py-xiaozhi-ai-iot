#!/usr/bin/env python3
"""
Test script để verify hello handshake hoạt động
"""
import asyncio
import json
import sys
from pathlib import Path

try:
    project_root = Path(__file__).resolve().parents[0]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
except Exception:
    pass

import websockets
from src.server.local_websocket_server import LocalWebSocketServer


async def test_server():
    """Khởi động server và test hello handshake"""
    print("🚀 Khởi động test server...")
    server = LocalWebSocketServer(host="localhost", port=9999)
    
    try:
        # Khởi động server
        await server.start()
        print("✅ Server đã khởi động tại ws://localhost:9999")
        
        # Test kết nối
        print("\n📞 Test kết nối từ client...")
        async with websockets.connect("ws://localhost:9999") as ws:
            # Gửi hello
            hello_msg = {
                "type": "hello",
                "version": 1,
                "features": {"mcp": True},
                "transport": "websocket",
                "audio_params": {
                    "format": "opus",
                    "sample_rate": 16000,
                    "channels": 1,
                    "frame_duration": 20,
                }
            }
            
            print(f"📤 Gửi hello message: {json.dumps(hello_msg)}")
            await ws.send(json.dumps(hello_msg))
            
            # Chờ phản hồi
            print("⏳ Chờ phản hồi hello...")
            response = await asyncio.wait_for(ws.recv(), timeout=5.0)
            response_data = json.loads(response)
            
            print(f"✅ Nhận phản hồi: {json.dumps(response_data, indent=2)}")
            
            # Kiểm tra xem transport field có trong response không
            if "transport" in response_data:
                print(f"✓ Transport field có trong response: '{response_data['transport']}'")
            else:
                print("✗ Transport field KHÔNG có trong response!")
                return False
            
            # Kiểm tra xem transport = "websocket"
            if response_data.get("transport") == "websocket":
                print("✓ Transport value là 'websocket' - ĐÚNG!")
            else:
                print(f"✗ Transport value sai: '{response_data.get('transport')}'")
                return False
            
            # Test listen command
            print("\n🎤 Test listen command...")
            listen_msg = {
                "type": "listen",
                "session_id": response_data.get("session_id"),
                "state": "start",
                "mode": "manual"
            }
            await ws.send(json.dumps(listen_msg))
            
            listen_response = await asyncio.wait_for(ws.recv(), timeout=5.0)
            listen_data = json.loads(listen_response)
            print(f"✅ Listen response: {json.dumps(listen_data, indent=2)}")
            
            # Test abort command
            print("\n⛔ Test abort command...")
            abort_msg = {
                "type": "abort",
                "session_id": response_data.get("session_id"),
                "reason": "test"
            }
            await ws.send(json.dumps(abort_msg))
            
            abort_response = await asyncio.wait_for(ws.recv(), timeout=5.0)
            abort_data = json.loads(abort_response)
            print(f"✅ Abort response: {json.dumps(abort_data, indent=2)}")
            
            print("\n🎉 Tất cả test đều thành công!")
            return True
            
    except asyncio.TimeoutError:
        print("❌ Timeout - server không phản hồi!")
        return False
    except Exception as e:
        print(f"❌ Lỗi: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await server.stop()
        print("\n🛑 Server đã dừng")


if __name__ == "__main__":
    success = asyncio.run(test_server())
    sys.exit(0 if success else 1)
