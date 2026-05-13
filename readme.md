# IqbalAI 1.0 - AI Teaching Assistant Application

An AI-powered educational platform providing intelligent tutoring, lesson management, and interactive learning experiences.

## 📚 Documentation

Comprehensive project documentation is available:

- **[PROJECT_DOCUMENTATION.md](PROJECT_DOCUMENTATION.md)** - Complete project documentation covering architecture, features, setup, configuration, API, security, deployment, and more
- **[API_REFERENCE.md](API_REFERENCE.md)** - Detailed API endpoint reference with request/response examples
- **[DEVELOPER_QUICK_START.md](DEVELOPER_QUICK_START.md)** - Quick start guide for developers
- **[docs/User_Guide.md](docs/User_Guide.md)** - User guide for end users
- **[app/rbac/README.md](app/rbac/README.md)** - Role-Based Access Control documentation
- **[docs/PHASE3.md](docs/PHASE3.md)** - Phase 3 student learning APIs, calendar sync, Celery reminders, group study, setup

## 🚀 Quick Start

### Prerequisites
- Python 3.8+
- PostgreSQL 12+ (or SQLite for development)
- pip

### Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd iqbalAI_1.0
   ```

2. **Create virtual environment**
   ```bash
   python -m venv venv
   # Windows:
   venv\Scripts\activate
   # Linux/Mac:
   source venv/bin/activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment**
   Create a `.env` file with your configuration (see [PROJECT_DOCUMENTATION.md](PROJECT_DOCUMENTATION.md#configuration))

5. **Run the application**
   ```bash
   python run.py
   ```

6. **Access the application**
   - URL: `http://localhost:5000`
   - Default admin: `admin` / `admin123` (change immediately!)

## ✨ Features

### Core Features
- **AI-Powered Chat**: Real-time conversational AI tutoring with context retention
- **User Authentication**: Secure registration, login, and password reset
- **Document Processing**: Upload and analyze PDFs, Word docs, and more
- **Lesson Management**: Teachers create and manage educational content
- **Subscription System**: Three-tier subscription with Stripe integration
- **Admin Dashboard**: Comprehensive admin interface for system management
- **RAG Integration**: Retrieval Augmented Generation for document-based responses
- **Multi-LLM Support**: OpenAI, Groq, and vLLM provider support

### User Roles
- **Students**: Access lessons, chat with AI, upload documents
- **Teachers**: Create lessons, upload materials, manage content
- **Administrators**: Full system access and management

## 📁 Project Structure

```
iqbalAI_1.0/
├── app/                    # Main application package
│   ├── routes/            # Route handlers (Controllers)
│   ├── models/            # Data models
│   ├── services/          # Business logic layer
│   ├── utils/             # Utility functions
│   ├── rbac/              # Role-Based Access Control
│   └── static/            # Static files (CSS, JS, images)
├── templates/             # HTML templates
├── instance/              # Instance-specific files
├── uploaded_files/        # User uploaded files
├── vector_stores/         # Vector store files
├── logs/                  # Application logs
├── docs/                  # Documentation
├── requirements.txt       # Python dependencies
└── run.py                 # Application entry point
```

## 🛠️ Technology Stack

- **Backend**: Flask, SQLAlchemy, PostgreSQL/SQLite
- **AI/ML**: LangChain, OpenAI, Groq, FAISS, Sentence Transformers
- **Frontend**: HTML5, CSS3, JavaScript, Tailwind CSS
- **Payment**: Stripe
- **Email**: Flask-Mail

## 📖 Additional Resources

- See [PROJECT_DOCUMENTATION.md](PROJECT_DOCUMENTATION.md) for complete documentation
- See [API_REFERENCE.md](API_REFERENCE.md) for API endpoints
- See [DEVELOPER_QUICK_START.md](DEVELOPER_QUICK_START.md) for development setup

## 🤝 Contributing

Please read the contributing guidelines in [PROJECT_DOCUMENTATION.md](PROJECT_DOCUMENTATION.md#contributing) before submitting pull requests.

## 📝 License

[Add license information]

## 📧 Contact

For issues, questions, or contributions, please create an issue in the repository.

---

**Version**: 1.0 | **Last Updated**: January 2025
