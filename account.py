"""
Simple Account Manager for Netflix Bot - Fixed OTP Expiration
"""

import logging
import re
import asyncio
from datetime import datetime, timedelta
from pyrogram import Client
from pyrogram.errors import (
    PhoneNumberInvalid, PhoneCodeInvalid,
    PhoneCodeExpired, SessionPasswordNeeded,
    FloodWait, PhoneCodeEmpty
)

logger = logging.getLogger(__name__)


class AccountManager:
    def __init__(self, api_id, api_hash):
        self.api_id = api_id
        self.api_hash = api_hash
        # Store sessions with timeout
        self.sessions = {}
    
    def send_otp(self, phone_number):
        """Send OTP to phone number"""
        try:
            result = asyncio.run(self._send_otp(phone_number))
            return result
        except Exception as e:
            logger.error(f"Send OTP error: {e}")
            return {
                "success": False,
                "error": f"Failed to send OTP: {str(e)}"
            }
    
    async def _send_otp(self, phone_number):
        """Async function to send OTP"""
        client = None
        try:
            client = Client(
                name=f"login_{int(datetime.now().timestamp())}",
                api_id=self.api_id,
                api_hash=self.api_hash,
                in_memory=True,
                no_updates=True
            )
            
            await client.connect()
            sent_code = await client.send_code(phone_number)
            
            # Create session data
            session_key = f"session_{int(datetime.now().timestamp())}"
            self.sessions[session_key] = {
                "phone": phone_number,
                "phone_code_hash": sent_code.phone_code_hash,
                "created_at": datetime.now(),
                "client_name": client.name
            }
            
            # Clean old sessions
            self._clean_old_sessions()
            
            await client.disconnect()
            
            logger.info(f"OTP sent to {phone_number}")
            
            return {
                "success": True,
                "phone_code_hash": sent_code.phone_code_hash,
                "session_key": session_key
            }
            
        except FloodWait as e:
            if client:
                try:
                    await client.disconnect()
                except:
                    pass
            return {
                "success": False,
                "error": f"Please wait {e.value} seconds"
            }
        except Exception as e:
            if client:
                try:
                    await client.disconnect()
                except:
                    pass
            return {
                "success": False,
                "error": str(e)
            }
    
    def verify_otp(self, session_key, otp_code, phone_number, phone_code_hash):
        """Verify OTP and get session string"""
        try:
            result = asyncio.run(self._verify_otp(session_key, otp_code, phone_number, phone_code_hash))
            return result
        except Exception as e:
            logger.error(f"Verify OTP error: {e}")
            return {
                "success": False,
                "error": f"OTP verification failed: {str(e)}"
            }
    
    async def _verify_otp(self, session_key, otp_code, phone_number, phone_code_hash):
        """Async function to verify OTP"""
        client = None
        try:
            # Check if session exists and is not expired
            if session_key not in self.sessions:
                return {
                    "success": False,
                    "error": "Session expired. Please start again with /start"
                }
            
            session_data = self.sessions[session_key]
            
            # Check if session is too old (more than 2 minutes)
            session_age = datetime.now() - session_data["created_at"]
            if session_age > timedelta(minutes=2):
                logger.warning(f"Session expired: {session_age.total_seconds()} seconds old")
                del self.sessions[session_key]
                return {
                    "success": False,
                    "error": "OTP session expired. Please request a new code with /start"
                }
            
            # Create new client for verification
            client = Client(
                name=f"verify_{int(datetime.now().timestamp())}",
                api_id=self.api_id,
                api_hash=self.api_hash,
                in_memory=True,
                no_updates=True
            )
            
            await client.connect()
            
            try:
                # Try to sign in
                await client.sign_in(
                    phone_number=phone_number,
                    phone_code=otp_code,
                    phone_code_hash=phone_code_hash
                )
                
                # Success - get session string
                session_string = await client.export_session_string()
                
                await client.disconnect()
                del self.sessions[session_key]
                
                return {
                    "success": True,
                    "session_string": session_string,
                    "has_2fa": False,
                    "two_step_password": None
                }
                
            except SessionPasswordNeeded:
                # 2FA required
                await client.disconnect()
                # Keep session for 2FA
                self.sessions[session_key]["needs_password"] = True
                return {
                    "success": False,
                    "needs_2fa": True,
                    "session_key": session_key
                }
                
            except PhoneCodeExpired:
                await client.disconnect()
                del self.sessions[session_key]
                return {
                    "success": False,
                    "error": "OTP code has expired. Please request a new code with /start"
                }
                
            except PhoneCodeInvalid:
                await client.disconnect()
                return {
                    "success": False,
                    "error": "Invalid OTP code. Please check and try again."
                }
                
            except Exception as e:
                await client.disconnect()
                del self.sessions[session_key]
                return {
                    "success": False,
                    "error": f"OTP verification failed: {str(e)}"
                }
                
        except Exception as e:
            if client:
                try:
                    await client.disconnect()
                except:
                    pass
            if session_key in self.sessions:
                del self.sessions[session_key]
            return {
                "success": False,
                "error": str(e)
            }
    
    def verify_2fa(self, session_key, password):
        """Verify 2FA password"""
        try:
            return asyncio.run(self._verify_2fa(session_key, password))
        except Exception as e:
            logger.error(f"Verify 2FA error: {e}")
            return {
                "success": False,
                "error": f"2FA verification failed: {str(e)}"
            }
    
    async def _verify_2fa(self, session_key, password):
        """Async function to verify 2FA"""
        client = None
        try:
            if session_key not in self.sessions:
                return {
                    "success": False,
                    "error": "Session expired. Please start again"
                }
            
            session_data = self.sessions[session_key]
            
            # Check if session is too old
            session_age = datetime.now() - session_data["created_at"]
            if session_age > timedelta(minutes=5):
                del self.sessions[session_key]
                return {
                    "success": False,
                    "error": "Session expired. Please start again"
                }
            
            client = Client(
                name=f"verify_2fa_{int(datetime.now().timestamp())}",
                api_id=self.api_id,
                api_hash=self.api_hash,
                in_memory=True,
                no_updates=True
            )
            
            await client.connect()
            
            try:
                await client.check_password(password)
                session_string = await client.export_session_string()
                
                await client.disconnect()
                del self.sessions[session_key]
                
                return {
                    "success": True,
                    "session_string": session_string,
                    "has_2fa": True,
                    "two_step_password": password
                }
                
            except Exception as e:
                await client.disconnect()
                del self.sessions[session_key]
                return {
                    "success": False,
                    "error": f"Password verification failed: {str(e)}"
                }
                
        except Exception as e:
            if client:
                try:
                    await client.disconnect()
                except:
                    pass
            if session_key in self.sessions:
                del self.sessions[session_key]
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_latest_otp(self, session_string, phone):
        """Get latest OTP from session"""
        try:
            return asyncio.run(self._get_latest_otp(session_string, phone))
        except Exception as e:
            logger.error(f"Get OTP error: {e}")
            return None
    
    async def _get_latest_otp(self, session_string, phone):
        """Async function to get OTP"""
        client = None
        try:
            client = Client(
                name=f"otp_fetch_{int(datetime.now().timestamp())}",
                session_string=session_string,
                api_id=self.api_id,
                api_hash=self.api_hash,
                in_memory=True,
                no_updates=True
            )
            
            await client.connect()
            
            latest_otp = None
            latest_time = None
            
            # Check Telegram messages
            try:
                async for message in client.get_chat_history("Telegram", limit=10):
                    if message.text and "code" in message.text.lower():
                        matches = re.findall(r'\b\d{5}\b', message.text)
                        if matches:
                            msg_time = message.date.timestamp() if message.date else 0
                            if not latest_time or msg_time > latest_time:
                                latest_time = msg_time
                                latest_otp = matches[0]
            except:
                pass
            
            await client.disconnect()
            return latest_otp
            
        except Exception as e:
            if client:
                try:
                    await client.disconnect()
                except:
                    pass
            return None
    
    def _clean_old_sessions(self):
        """Clean up old sessions"""
        try:
            cutoff = datetime.now() - timedelta(minutes=10)
            to_delete = []
            
            for key, data in self.sessions.items():
                if data["created_at"] < cutoff:
                    to_delete.append(key)
            
            for key in to_delete:
                del self.sessions[key]
            
            if to_delete:
                logger.info(f"Cleaned {len(to_delete)} old sessions")
                
        except Exception as e:
            logger.error(f"Clean sessions error: {e}")
