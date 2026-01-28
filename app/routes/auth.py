from flask import Blueprint, request, session, redirect, url_for, render_template, jsonify
from app.models import UserModel
from app.utils.db import get_db
from app.models.database_models import EmailVerificationToken as DBEmailVerificationToken, PasswordResetToken as DBPasswordResetToken
from sqlalchemy import and_
import logging
import requests
import secrets
from datetime import datetime, timedelta
from flask_mail import Message
from app import mail
from app.config import Config
import os

logger = logging.getLogger(__name__)
bp = Blueprint('auth', __name__)

@bp.route('/register_email', methods=['GET', 'POST'])
def register_email():
    if request.method == 'POST':
        try:
            email = request.form['useremail']
            
            # Check if email already exists
            if UserModel.get_user_by_email(email):
                return render_template('register_email.html', error="Email already registered")
            
            # Generate verification token
            token = secrets.token_urlsafe(32)
            expires_at = datetime.utcnow() + timedelta(hours=24)
            
            # Store token in database
            db = get_db()
            
            # Delete any existing tokens for this email
            db.query(DBEmailVerificationToken).filter(
                DBEmailVerificationToken.email == email
            ).delete()
            
            # Create new token
            verification_token = DBEmailVerificationToken(
                token=token,
                email=email,
                expires_at=expires_at,
                used=False
            )
            db.add(verification_token)
            db.commit()
            
            # Send verification email
            msg = Message('Verify your email',
                        recipients=[email])
            
            # Generate verification link - handle production vs development
            # Detect if we're running locally or in production
            is_local = (
                request.host.startswith('localhost') or 
                request.host.startswith('127.0.0.1') or
                request.host.startswith('0.0.0.0') or
                'localhost' in request.host or
                '127.0.0.1' in request.host
            )
            
            # Check if behind reverse proxy (production)
            is_production = 'X-Forwarded-Host' in request.headers or not is_local
            
            if is_production and not is_local:
                # Production environment - use SERVER_URL or X-Forwarded-Host
                server_url = os.getenv('SERVER_URL') or getattr(Config, 'SERVER_URL', None)
                
                if 'X-Forwarded-Host' in request.headers:
                    # Behind reverse proxy (nginx, etc.) - use forwarded headers
                    scheme = request.headers.get('X-Forwarded-Proto', 'https')
                    host = request.headers['X-Forwarded-Host']
                    verification_link = f"{scheme}://{host}/auth/verify_email/{token}"
                elif server_url:
                    # Use configured server URL (for production)
                    verification_link = f"{server_url.rstrip('/')}/auth/verify_email/{token}"
                else:
                    # Fallback to url_for with external=True
                    verification_link = url_for('auth.verify_email',
                                            token=token,
                                            _external=True,
                                            _scheme=request.scheme)
            else:
                # Local development - use request-based URL generation
                verification_link = url_for('auth.verify_email',
                                          token=token,
                                          _external=True,
                                          _scheme=request.scheme)
            
            msg.body = f'''Please click the following link to verify your email and complete registration:
{verification_link}

This link will expire in 24 hours.

If clicking the link doesn't work, please copy and paste it into your browser.'''
            
            mail.send(msg)
            logger.info(f"Verification email sent to {email} with link: {verification_link}")
            
            return render_template('email_sent.html', email=email)
            
        except Exception as e:
            logger.error(f"Email registration error: {str(e)}")
            return render_template('register_email.html', error="Failed to send verification email")
            
    return render_template('register_email.html')

@bp.route('/verify_email/<token>')
def verify_email(token):
    try:
        logger.info(f"Verifying email with token: {token[:10]}...")  # Log only first 10 chars for security
        
        db = get_db()
        
        # Query token from database
        verification_token = db.query(DBEmailVerificationToken).filter(
            and_(
                DBEmailVerificationToken.token == token,
                DBEmailVerificationToken.used == False
            )
        ).first()
        
        if not verification_token:
            logger.warning(f"Invalid token attempted: {token[:10]}...")
            return render_template('register.html', error="Invalid or expired verification link")
        
        # Check if token has expired - use UTC for consistency
        current_time = datetime.utcnow()
        expires_at = verification_token.expires_at
        
        # Log expiration details for debugging
        logger.debug(f"Token expiration check - Current: {current_time}, Expires: {expires_at}, Email: {verification_token.email}")
        
        if current_time > expires_at:
            # Mark as used to clean up
            verification_token.used = True
            db.commit()
            time_diff = current_time - expires_at
            logger.warning(f"Expired token attempted: {token[:10]}... (expired {time_diff} ago)")
            return render_template('register.html', error="Verification link has expired")
        
        email = verification_token.email
        logger.info(f"Email verified successfully for: {email}")
        
        # Keep the token valid until registration is complete (don't mark as used yet)
        return render_template('register.html', email=email)
        
    except Exception as e:
        logger.error(f"Error in email verification: {str(e)}")
        db.rollback()
        return render_template('register.html', error="An error occurred during verification. Please try again.")

@bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        try:
            # Log request details for debugging
            logger.debug(f"Register request - Content-Type: {request.content_type}")
            logger.debug(f"Register request - Form data: {dict(request.form)}")
            logger.debug(f"Register request - JSON data: {request.get_json(silent=True)}")
            
            # Check if form data exists
            if not request.form:
                logger.error("No form data received in registration request")
                return render_template('register.html', error="Invalid request format. Please ensure the form is submitted correctly.")
            
            # Get email - handle both form and potential JSON
            email = request.form.get('useremail')
            if not email:
                logger.error("Missing email in registration request")
                return render_template('register.html', error="Email is required")
            
            # Verify that email was previously verified
            db = get_db()
            verification_token = db.query(DBEmailVerificationToken).filter(
                and_(
                    DBEmailVerificationToken.email == email,
                    DBEmailVerificationToken.used == False,
                    DBEmailVerificationToken.expires_at > datetime.utcnow()
                )
            ).first()
                    
            if not verification_token:
                return render_template('register.html', error="Email not verified")
            
            # Get all required form fields with validation
            username = request.form.get('username')
            password = request.form.get('password')
            class_standard = request.form.get('class_standard', '')  # Optional field - defaults to empty string
            medium = request.form.get('medium')
            groq_api_key = request.form.get('groq_api_key', '')  # Optional field - defaults to empty string
            role = request.form.get('role')
            
            # Validate required fields (groq_api_key and class_standard are optional, so not included in validation)
            missing_fields = []
            if not username:
                missing_fields.append('username')
            if not password:
                missing_fields.append('password')
            if not medium:
                missing_fields.append('medium')
            if not role:
                missing_fields.append('role')
            
            if missing_fields:
                logger.error(f"Missing required fields: {missing_fields}")
                return render_template('register.html', error=f"Missing required fields: {', '.join(missing_fields)}")
            
            user_id = UserModel.create_user(
                username=username,
                useremail=email,
                password=password,
                class_standard=class_standard,
                medium=medium,
                groq_api_key=groq_api_key,  # Will default to empty string if not provided
                role=role
            )
            
            # Mark verification token as used and clean up all tokens for this email
            db.query(DBEmailVerificationToken).filter(
                DBEmailVerificationToken.email == email
            ).update({DBEmailVerificationToken.used: True})
            db.commit()
                    
            return redirect(url_for('auth.login'))
        except ValueError as e:
            logger.error(f"Registration validation error: {str(e)}")
            return render_template('register.html', error=str(e))
        except KeyError as e:
            logger.error(f"Missing form field: {str(e)}")
            return render_template('register.html', error=f"Missing required field: {str(e)}")
        except Exception as e:
            logger.error(f"Registration error: {str(e)}", exc_info=True)
            return render_template('register.html', error="Registration failed. Please try again.")
            
    return render_template('register.html')

@bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        try:
            user = UserModel.get_user_by_email(request.form['useremail'])
            if user and user['password'] == request.form['password']:
                session.clear()
                session['user_id'] = user['id']
                session['username'] = user['username']
                session['role'] = user.get('role', 'student')  # Store role in session
                session['groq_api_key'] = user.get('groq_api_key', '')  # Default to empty string if not present
                session.permanent = True  # Make session permanent for 24 hours
                
                # Redirect admins to admin dashboard
                if user.get('role') == 'admin':
                    return redirect('/admin/')
                
                return redirect(url_for('chat.index'))
            return render_template('login.html', error="Invalid credentials")
        except Exception as e:
            logger.error(f"Login error: {str(e)}")
            return render_template('login.html', error="Login failed")
    return render_template('login.html')

# @bp.route('/logout',methods=['GET','POST'])
# def logout():
#     session.clear()
#     return redirect(url_for('auth.login'))

# @bp.route('/logout', methods=['GET', 'POST'])
# def logout():
#     # Debugging: Print session before clearing
#     print("Session before clear:", session)
    
#     # Clear all session data
#     session.clear()
    
#     # Debugging: Print session after clearing
#     print("Session after clear:", session)
    
#     # Create redirect response
#     login_url = url_for('auth.login')  # Ensure this matches your login route
#     response = redirect(login_url)
    
#     # Add cache-control headers
#     response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
#     response.headers['Pragma'] = 'no-cache'
#     response.headers['Expires'] = '0'
    
#     # Add header to prevent back button access
#     response.headers['Cache-Control'] = 'no-store'
    
#     return response




@bp.route('/logout', methods=['GET', 'POST'])
def logout():
    try:
        session.clear()
        # Handle AJAX requests differently
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            login_url = url_for('auth.login')
            return jsonify({
                'success': True,
                'redirect_url': login_url
            }), 200
        # For regular requests, redirect normally
        login_url = url_for('auth.login')
        response = redirect(login_url)
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response
    except Exception as e:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'error': str(e)}), 500
        return redirect(url_for('auth.login'))














@bp.route('/check_session')
def check_session():
    if 'user_id' in session:
        return {'logged_in': True, 'username': session.get('username')}
    return {'logged_in': False}, 401

@bp.route('/session', methods=['GET'])
def get_session():
    """Get OpenAI realtime session token"""
    try:
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            return jsonify({
                'error': 'OpenAI API key not found in session'
            }), 401

        url = "https://api.openai.com/v1/realtime/sessions"
        
        payload = {
            "model": "gpt-4o-realtime-preview-2024-12-17",
            "modalities": ["audio", "text"],
            "instructions": """Mr. Potter's Teaching Philosophy and Methodology

       

    A: Prof. Potter greets by saying: “Hello, my name is Prof. Potter, how can I help you today?” Mr. Potter responds as requested. 
    B: Prof. Potter’s demeanor and his approach to helping Faculty prepare lesson plans for their students
    •	You, Prof. Potter, are an experienced teacher who assists the Faculty in preparing lessons for the Faculty’s students. 
    •	Prof. Potter remembers Faculty names, their subjects of interest, and conversations. 
    •	Prof Potter helps the Faculty generate lesson plans and at the grade that the faculty is teaching
    •	Prof. Potter listens and synthesizes the Faculty’s request clearly and delivers a response 
    •	Prof Potter makes frequent suggestions to faculty to use simpler and creative vocabulary in teaching that is more in tune with the student’s understanding and grade level that the faculty is teaching.
    •	For each question from the faculty, Prof. Potter repeats the question to himself twice and only responds if both of his internal answers match.
    •	Insists that the Faculty be creative and generate creative lessons.
    •	Commends faculty when they bring a perspective during the interaction of lesson generation that was not being discussed, and especially when it is unknown to LLM.  
    •	Prof. Potter let it be known in the course of their lesson development that when the Faculty brings up a new perspective in teaching, a new approach to introducing a concept to the students, or introduces an idea unknown to Mr. Potter, Mr. Potter commends the Faculty for being creative and encourages further creativity. Mr. Potter let it be known to the Faculty that such effort is considered impressive, encouraged, noted, and rewarded.
    C: Prof. Potter’s process for answering the Faculty's questions is as follows: 
    •	Prof. Potter summons vast knowledge from the internet; seeks the “best” approach to respond to the Faculty’s question, by the “best” means people online have responded positively, and a method widely used in teaching.
    •	Prof. Potter adapts to the Faculty's student level, and takes the following approach to meet the Faculty’s needs.
    •	First, the Faculty asks Prof. Potter a question related to the lesson plan generation. Prof. Potter does not respond to the Faculty’s request but asks if the Faculty prefers to revise the background material first in the lesson. The student must have an understanding of the prerequisites for them to learn the lesson that the Faculty plans to deliver.
    •	If the Faculty agrees to cover brief background material related directly to the lesson to be delivered, Prof. Potter provides a brief background material related to the lesson that the Faculty plans to deliver to his student. This conversation continues until the faculty is satisfied and acknowledges to proceed.  
    •	 Continuing, Prof. Potter's teaching strategy is to break the Faculty’s lesson plan into a series of “simpler short lectures.” Combining all the series of “simpler short lectures” is the Faculty's lesson to be generated for his students. 
    •	Prof. Potter summons vast knowledge from the internet to answer, one at a time, these “simpler short lectures” that Prof Potter had created, moving to the next “simpler short lectures” with the Faculty's acknowledgement.
    •	Occasionally, keep the Faculty informed of where you are in the context of the lesson generation 
    •	Each of the “simpler short lectures” must be self-explanatory, and Prof. Potter combines all the series of “simpler short lectures” generated; is the Faculty's lesson for his students. 
    Prof. Potter's approach to Teaching   
    •	Understand the Faculty's lesson generation needs and state the lesson’s underlying general concept.     
    •	When the Faculty has to give a lesson on STEM subjects, Prof. Potter examines the following and their relations with each other. 
    o	Contextual Analysis: Examines lesson structure, vocabulary, and implied concepts
    o	Equation Mapping: Searches comprehensive mathematical databases to identify relevant equations to be provided as a part of the lesson to be delivered by Faculty
    o	Adjusts the equation’s explanation based on the Faculty's student' educational grade or the Faculty’s own opinion about their students’ understanding level.
    o	Context Preservation: Maintains equation information throughout extended lessons
    o	This is Prof. Potter's most important objective: From delivering past lessons to students, Faculty understands their students’ learning challenges; Prof. Potter is also perceptive, and derives students’ caliber from the interaction with Faculty’s lesson content generation activities and the pace of Faculty’s teaching. If the perception derived from the Faculty’s interaction with Prof. Potter during lesson generation is that the students are lagging compared to general student population of the same grade, Prof. Potter offers suggestions in form of variety of approach in the lesson plan generation, and subtly continues to press the Faculty to challenge the students and bring their caliber to same level as general student body of the same grade level. During every lesson, Prof. Potter continues to strive to help the Faculty propel their students to the highest standard. 

    Teaching Protocol: Equation-Based Approach
    After Prof. Potter has identified the equation that addresses the Faculty's lesson plan requirements, Prof. Potter must follow these steps in lesson generation without revealing the complete equation to the Faculty until Step 5:
    Step 1: Individual Term Explanation
    •	Explain each term in the equation one at a time
    •	Define what each term means physically in the real world
    •	Do not show any mathematical relationships or operations yet
    Step 2: Mathematical Operations 
    When a term has a mathematical operator applied to it, explain in this exact order:
    •	First: What the individual term means by itself
    •	Second: What the mathematical operator does to that term
    •	Third: What the combination produces physically
    Example:
    •	Position (x) by itself = the location of an object in space
    •	Differentiation operator (d/dt) = finding the rate of change with respect to time
    •	Differentiation applied to position (dx/dt) = velocity (how fast location changes over time)
    Step 3: Check for Understanding
    •	After explaining each term or operation, ask the Faculty if they understand using varied, engaging questions
    •	Provide additional clarification as needed before proceeding to the next term
    •	Do not continue until the Faculty demonstrates understanding of Prof. Potter’s approach

    Step 4: Complete All Terms
    •	Repeat Steps 1-3 for every single term in the equation
    •	Ensure each term and its operations are fully understood before moving to the next term
    Step 5: Synthesize the Complete Equation
    •	Connect all the previously explained terms together 
    •	Now reveal the complete equation for the first time. 
    •	Explain the significance of each term's position in the equation (numerator vs. denominator, exponents, powers, coefficients)
    •	Help the Faculty visualize how the equation behaves in the real world. Revealing the complete equation, Prof. Potter explains the connection between math and science concepts. 
    •	Provide a comprehensive explanation of how this complete equation answers the lesson plan that the Faculty requested to be generated.
    Critical Rule: The complete equation must remain hidden until Step 5 is reached. 
    •	Ask questions to determine if the Faculty is grasping the lesson’s approach. Ask if the approach taken by Prof. Potter explains the concept to Faculty’s satisfaction. Prof. Potter responds to the Faculty’s request according to the needs and desires of the Faculty. Prof. Potter helps generate clear descriptions without any ambiguity, describing exactly how the concept and equations are connected and how they work in real world. Prof. Potter, with Faculty interaction during the lesson plan generation, makes sure, exactly and clearly, that students will understand how to use the equation to solve problems in exams and in the real world.

    Challenge the Faculty by asking in depth questions or offering advice: Teaching Speed vs. Velocity (Apply this depth to every STEM concept)
    Required Teaching Method:
    Classification: Identify speed as scalar (magnitude only), velocity as vector (magnitude + direction)
    1.	Definition: Speed = numerical value only; Velocity = numerical value with specific direction
    2.	Real-world visualization: Use map analogy - speedometer shows speed (just number), GPS shows velocity (speed + direction to destination)
    3.	Practical application: Faculty must demonstrate understanding by identifying and using both concepts in real situations
    MANDATORY REQUIREMENTS FOR EVERY STEM CONCEPT:
    •	Dimensional Analysis: State dimensions and units for every term (physics, chemistry, engineering)
    •	Classification: Identify relevant properties (scalar/vector, acid/base, organic/inorganic, etc.)
    •	Real-World Behavior: Explain exactly how the concept works in reality using concrete examples
    •	Visual Understanding: Provide analogies, diagrams, models, or real-world scenarios
    •	Mastery Verification: Faculty must independently explain, apply, and distinguish the concept
    THIS STANDARD APPLIES TO EVERY EQUATION, FORMULA, TERM, AND CONCEPT IN MATHEMATICS, PHYSICS, CHEMISTRY, BIOLOGY, AND ENGINEERING - NO EXCEPTIONS.





	












"""
        }
        
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }

        response = requests.post(url, json=payload, headers=headers)
        
        if response.status_code != 200:
            return jsonify({
                'error': 'Failed to get session token'
            }), response.status_code

        return response.json()

    except requests.exceptions.RequestException as e:
        logger.error(f"Network error getting session token: {str(e)}")
        return jsonify({'error': 'Network error occurred'}), 500
    except Exception as e:
        logger.error(f"Error getting session token: {str(e)}")
        return jsonify({'error': str(e)}), 500

@bp.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        try:
            email = request.form['useremail']
            
            # Check if email exists
            user = UserModel.get_user_by_email(email)
            if not user:
                return render_template('forgot_password.html', error="Email not found")
            
            # Generate OTP
            otp = ''.join([str(secrets.randbelow(10)) for _ in range(6)])
            expires_at = datetime.utcnow() + timedelta(minutes=15)
            
            # Store OTP in database
            db = get_db()
            
            # Delete any existing OTPs for this email
            db.query(DBPasswordResetToken).filter(
                DBPasswordResetToken.email == email
            ).delete()
            
            # Create new OTP token
            reset_token = DBPasswordResetToken(
                email=email,
                otp=otp,
                expires_at=expires_at,
                used=False
            )
            db.add(reset_token)
            db.commit()
            
            # Send OTP email
            msg = Message('Password Reset OTP',
                        recipients=[email])
            
            msg.body = f'''Your password reset OTP is: {otp}

This OTP will expire in 15 minutes.

If you didn't request this password reset, please ignore this email.'''
            
            mail.send(msg)
            logger.info(f"Password reset OTP sent to {email}")
            
            return render_template('reset_password.html', email=email)
            
        except Exception as e:
            logger.error(f"Password reset error: {str(e)}")
            return render_template('forgot_password.html', error="Failed to send OTP")
            
    return render_template('forgot_password.html')

@bp.route('/reset_password', methods=['GET', 'POST'])
def reset_password():
    if request.method == 'POST':
        try:
            email = request.form['useremail']
            otp = request.form['otp']
            new_password = request.form['new_password']
            confirm_password = request.form['confirm_password']
            
            # Validate passwords match
            if new_password != confirm_password:
                return render_template('reset_password.html', email=email, error="Passwords do not match")
            
            # Check if OTP exists and is valid
            db = get_db()
            reset_token = db.query(DBPasswordResetToken).filter(
                and_(
                    DBPasswordResetToken.email == email,
                    DBPasswordResetToken.used == False
                )
            ).first()
            
            if not reset_token:
                return render_template('reset_password.html', email=email, error="Invalid or expired OTP")
            
            # Check if expired
            if datetime.utcnow() > reset_token.expires_at:
                reset_token.used = True
                db.commit()
                return render_template('reset_password.html', email=email, error="OTP has expired")
            
            # Verify OTP
            if reset_token.otp != otp:
                return render_template('reset_password.html', email=email, error="Invalid OTP")
            
            # Update password in database
            from app.models.database_models import User as DBUser
            user = db.query(DBUser).filter(DBUser.useremail == email).first()
            if user:
                user.password = new_password
                db.commit()
            
            # Mark reset token as used
            reset_token.used = True
            db.commit()
            
            return redirect(url_for('auth.login'))
            
        except Exception as e:
            logger.error(f"Password reset error: {str(e)}")
            return render_template('reset_password.html', email=email, error="Failed to reset password")
            
    return redirect(url_for('auth.forgot_password'))