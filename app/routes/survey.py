from flask import Blueprint, request, jsonify
from flask_cors import cross_origin
from app.models.models import SurveyModel
from app.models.database_models import SurveyResponse as DBSurveyResponse
from app.utils.auth import login_required
# from app.utils.decorators import login_required
from app.utils.logger import logger
from app.utils.db import get_db

bp = Blueprint('survey', __name__)

@bp.route('/survey', methods=['POST', 'OPTIONS'])
@cross_origin(origins=['http://localhost:3000', 'http://localhost:8080', 'http://127.0.0.1:3000'], 
              supports_credentials=True,
              allow_headers=['Content-Type', 'Authorization'])
@login_required
def submit_survey():
    """Submit a survey response"""
    # Handle preflight OPTIONS request
    if request.method == 'OPTIONS':
        response = jsonify({'status': 'ok'})
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', 'http://localhost:3000'))
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
        response.headers.add('Access-Control-Allow-Methods', 'POST')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response
    
    try:
        logger.info(f"Survey submission request received for user_id: {request.user_id}")
        data = request.get_json()
        logger.info(f"Received survey data: {data}")
        
        if not data:
            logger.error("No data provided in survey submission")
            response = jsonify({'error': 'No data provided'})
            response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', 'http://localhost:3000'))
            response.headers.add('Access-Control-Allow-Credentials', 'true')
            return response, 400
            
        rating = data.get('rating')
        message = data.get('message')
        
        logger.info(f"Processing survey - Rating: {rating} (type: {type(rating)}), Message: {message}, User ID: {request.user_id}")
        
        if not rating or not isinstance(rating, int) or rating < 1 or rating > 10:
            logger.error(f"Invalid rating: {rating} (type: {type(rating)})")
            response = jsonify({'error': 'Invalid rating. Must be an integer between 1 and 10'})
            response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', 'http://localhost:3000'))
            response.headers.add('Access-Control-Allow-Credentials', 'true')
            return response, 400
        
        # Check if user has already submitted
        survey_model = SurveyModel(user_id=request.user_id)
        if survey_model.has_submitted_survey():
            logger.warning(f"User {request.user_id} attempted to submit multiple surveys")
            response = jsonify({'error': 'Survey already submitted'})
            response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', 'http://localhost:3000'))
            response.headers.add('Access-Control-Allow-Credentials', 'true')
            return response, 400
        
        # Save the survey response
        logger.info(f"Saving survey response - User: {request.user_id}, Rating: {rating}, Message: {message}")
        response_id = survey_model.save_survey_response(rating=rating, message=message)
        
        logger.info(f"Survey successfully submitted - User ID: {request.user_id}, Rating: {rating}, Response ID: {response_id}")
        
        response = jsonify({
            'success': True,
            'message': 'Survey response submitted successfully',
            'response_id': response_id
        })
        
        # Add CORS headers to successful response
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', 'http://localhost:3000'))
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        
        return response, 201
        
    except Exception as e:
        logger.error(f"Error submitting survey: {str(e)}")
        logger.exception("Full traceback:")
        response = jsonify({'error': f'Failed to submit survey response: {str(e)}'})
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', 'http://localhost:3000'))
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response, 500

@bp.route('/survey/responses', methods=['GET', 'OPTIONS'])
@cross_origin(origins=['http://localhost:3000', 'http://localhost:8080', 'http://127.0.0.1:3000'], 
              supports_credentials=True,
              allow_headers=['Content-Type', 'Authorization'])
@login_required
def get_survey_responses():
    """Get all survey responses for the current user"""
    # Handle preflight OPTIONS request
    if request.method == 'OPTIONS':
        response = jsonify({'status': 'ok'})
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', 'http://localhost:3000'))
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
        response.headers.add('Access-Control-Allow-Methods', 'GET')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response
    
    try:
        survey_model = SurveyModel(user_id=request.user_id)
        responses = survey_model.get_user_survey_responses()
        
        response = jsonify({
            'responses': responses
        })
        
        # Add CORS headers
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', 'http://localhost:3000'))
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        
        return response, 200
        
    except Exception as e:
        logger.error(f"Error retrieving survey responses: {str(e)}")
        response = jsonify({'error': 'Failed to retrieve survey responses'})
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', 'http://localhost:3000'))
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response, 500 

@bp.route('/check_survey_status', methods=['GET', 'OPTIONS'])
@cross_origin(origins=['http://localhost:3000', 'http://localhost:8080', 'http://127.0.0.1:3000'], 
              supports_credentials=True,
              allow_headers=['Content-Type', 'Authorization'])
@login_required
def check_survey_status():
    """Check if user has submitted a survey"""
    # Handle preflight OPTIONS request
    if request.method == 'OPTIONS':
        response = jsonify({'status': 'ok'})
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', 'http://localhost:3000'))
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
        response.headers.add('Access-Control-Allow-Methods', 'GET')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response
    
    try:
        logger.info(f"Checking survey status for user_id: {request.user_id}")
        survey_model = SurveyModel(user_id=request.user_id)
        has_submitted = survey_model.has_submitted_survey()
        
        status_msg = f"User {request.user_id} has {'submitted' if has_submitted else 'not submitted'} the survey"
        logger.info(status_msg)
        print(f"\n=== Survey Status ===\n{status_msg}\n===================")
        
        response = jsonify({
            'has_submitted': has_submitted
        })
        
        # Add CORS headers
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', 'http://localhost:3000'))
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        
        return response, 200
        
    except Exception as e:
        logger.error(f"Error checking survey status: {str(e)}")
        logger.exception("Full traceback:")
        response = jsonify({'error': 'Failed to check survey status'})
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', 'http://localhost:3000'))
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response, 500

@bp.route('/reset_survey', methods=['POST', 'OPTIONS'])
@cross_origin(origins=['http://localhost:3000', 'http://localhost:8080', 'http://127.0.0.1:3000'], 
              supports_credentials=True,
              allow_headers=['Content-Type', 'Authorization'])
@login_required
def reset_survey():
    """Reset survey responses for the current user (for testing)"""
    # Handle preflight OPTIONS request
    if request.method == 'OPTIONS':
        response = jsonify({'status': 'ok'})
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', 'http://localhost:3000'))
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
        response.headers.add('Access-Control-Allow-Methods', 'POST')
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response
    
    try:
        logger.info(f"Resetting survey responses for user_id: {request.user_id}")
        
        db = get_db()
        db.query(DBSurveyResponse).filter(DBSurveyResponse.user_id == request.user_id).delete()
        db.commit()
        
        logger.info(f"Survey responses reset for user {request.user_id}")
        
        response = jsonify({
            'success': True,
            'message': 'Survey responses reset successfully'
        })
        
        # Add CORS headers
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', 'http://localhost:3000'))
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        
        return response, 200
        
    except Exception as e:
        logger.error(f"Error resetting survey responses: {str(e)}")
        logger.exception("Full traceback:")
        response = jsonify({'error': 'Failed to reset survey responses'})
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', 'http://localhost:3000'))
        response.headers.add('Access-Control-Allow-Credentials', 'true')
        return response, 500