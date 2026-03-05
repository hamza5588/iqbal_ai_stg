/**
 * Comprehensive Dummy Data for Iqbal AI Platform
 * Provides realistic demo data for all user roles: Students, Teachers, and Admins
 * Enable demo mode by setting DEMO_MODE_ENABLED = true
 */

// ============================================================================
// DEMO MODE CONFIGURATION
// ============================================================================

const DEMO_CONFIG = {
  DEMO_MODE_ENABLED: false, // Toggle demo mode on/off (disabled for real users)
  AUTO_LOAD_DEMO_DATA: false, // Do NOT auto-load demo data in production/staging
  USE_MOCK_API: false, // Use real API calls instead of mock responses
  DEBUG_MODE: false // Log demo data operations
};

// ============================================================================
// DEMO USERS DATA
// ============================================================================

const DEMO_USERS = {
  students: [
    {
      id: 'user-s1',
      email: 'student@iqbalai.com',
      name: 'Student User',
      role: 'student',
      avatar: 'https://api.dicebear.com/7.x/avataaars/svg?seed=Student1',
      grade: 'Grade 10',
      school: 'Iqbal Academy',
      bio: 'Passionate learner exploring various subjects',
      joinDate: '2024-01-15',
      coursesEnrolled: 5,
      lessonsCompleted: 12,
      totalStudyHours: 45
    },
    {
      id: 'user-s2',
      email: 'sarah@iqbal.edu',
      name: 'Sarah Johnson',
      role: 'student',
      avatar: 'https://api.dicebear.com/7.x/avataaars/svg?seed=Sarah',
      grade: 'Grade 10',
      school: 'Iqbal Academy',
      bio: 'Science enthusiast and debate champion',
      joinDate: '2024-02-01',
      coursesEnrolled: 6,
      lessonsCompleted: 18,
      totalStudyHours: 72
    },
    {
      id: 'user-s3',
      email: 'michael@iqbal.edu',
      name: 'Michael Chen',
      role: 'student',
      avatar: 'https://api.dicebear.com/7.x/avataaars/svg?seed=Michael',
      grade: 'Grade 11',
      school: 'Iqbal Academy',
      bio: 'Mathematics lover preparing for competitions',
      joinDate: '2024-02-10',
      coursesEnrolled: 4,
      lessonsCompleted: 15,
      totalStudyHours: 60
    },
    {
      id: 'user-s4',
      email: 'john.student@iqbal.edu',
      name: 'John Doe',
      role: 'student',
      avatar: 'https://api.dicebear.com/7.x/avataaars/svg?seed=John',
      grade: 'Grade 9',
      school: 'Iqbal Academy',
      bio: 'Curious learner with interest in languages',
      joinDate: '2024-03-05',
      coursesEnrolled: 5,
      lessonsCompleted: 8,
      totalStudyHours: 32
    },
    {
      id: 'user-s5',
      email: 'emma@iqbal.edu',
      name: 'Emma Wilson',
      role: 'student',
      avatar: 'https://api.dicebear.com/7.x/avataaars/svg?seed=Emma',
      grade: 'Grade 10',
      school: 'Iqbal Academy',
      bio: 'Creative writer and literature enthusiast',
      joinDate: '2024-03-12',
      coursesEnrolled: 3,
      lessonsCompleted: 10,
      totalStudyHours: 28
    }
  ],

  teachers: [
    {
      id: 'user-t1',
      email: 'teacher@iqbalai.com',
      name: 'Teacher User',
      role: 'teacher',
      avatar: 'https://api.dicebear.com/7.x/avataaars/svg?seed=Teacher1',
      subject: 'General Studies',
      qualification: 'B.Ed',
      experience: '5 years',
      bio: 'Experienced educator committed to student success',
      joinDate: '2024-01-10',
      studentsManaged: 45,
      lessonsCreated: 12,
      averageRating: 4.5
    },
    {
      id: 'user-t2',
      email: 'john@iqbal.edu',
      name: 'John Smith',
      role: 'teacher',
      avatar: 'https://api.dicebear.com/7.x/avataaars/svg?seed=JohnSmith',
      subject: 'Mathematics',
      qualification: 'M.Sc (Mathematics)',
      experience: '8 years',
      bio: 'Mathematics expert specializing in competitive exams',
      joinDate: '2024-01-05',
      studentsManaged: 87,
      lessonsCreated: 28,
      averageRating: 4.8
    },
    {
      id: 'user-t3',
      email: 'emily@iqbal.edu',
      name: 'Emily Davis',
      role: 'teacher',
      avatar: 'https://api.dicebear.com/7.x/avataaars/svg?seed=Emily',
      subject: 'English & Literature',
      qualification: 'M.A (English)',
      experience: '6 years',
      bio: 'Passionate about literature and creative writing',
      joinDate: '2024-01-20',
      studentsManaged: 62,
      lessonsCreated: 18,
      averageRating: 4.7
    },
    {
      id: 'user-t4',
      email: 'david@iqbal.edu',
      name: 'David Martinez',
      role: 'teacher',
      avatar: 'https://api.dicebear.com/7.x/avataaars/svg?seed=David',
      subject: 'Science',
      qualification: 'M.Sc (Physics)',
      experience: '10 years',
      bio: 'Physics and Science educator with research background',
      joinDate: '2024-01-08',
      studentsManaged: 95,
      lessonsCreated: 35,
      averageRating: 4.9
    },
    {
      id: 'user-t5',
      email: 'sophia@iqbal.edu',
      name: 'Sophia Anderson',
      role: 'teacher',
      avatar: 'https://api.dicebear.com/7.x/avataaars/svg?seed=Sophia',
      subject: 'History & Social Studies',
      qualification: 'M.A (History)',
      experience: '7 years',
      bio: 'History educator making past come alive for students',
      joinDate: '2024-01-12',
      studentsManaged: 73,
      lessonsCreated: 22,
      averageRating: 4.6
    }
  ],

  admins: [
    {
      id: 'user-a1',
      email: 'admin@iqbalai.com',
      name: 'Admin User',
      role: 'admin',
      avatar: 'https://api.dicebear.com/7.x/avataaars/svg?seed=Admin1',
      department: 'Platform Management',
      joinDate: '2023-12-01',
      permissions: ['manage_users', 'manage_content', 'manage_settings', 'analytics', 'coupons']
    },
    {
      id: 'user-a2',
      email: 'admin@iqbal.edu',
      name: 'System Admin',
      role: 'admin',
      avatar: 'https://api.dicebear.com/7.x/avataaars/svg?seed=Admin2',
      department: 'System Administration',
      joinDate: '2023-12-05',
      permissions: ['manage_users', 'manage_content', 'manage_settings', 'analytics', 'coupons', 'system_config']
    }
  ]
};

// ============================================================================
// DEMO LESSONS & COURSES
// ============================================================================

const DEMO_LESSONS = [
  {
    id: 'lesson-1',
    title: 'Introduction to Photosynthesis',
    subject: 'Biology',
    grade: 'Grade 9-10',
    teacher: 'David Martinez',
    teacherId: 'user-t4',
    description: 'Learn how plants convert sunlight into chemical energy through photosynthesis',
    duration: '45 min',
    difficulty: 'Beginner',
    students: 42,
    rating: 4.8,
    thumbnail: 'https://images.unsplash.com/photo-1574263867373-f78145dd23f7?w=400&h=300&fit=crop',
    content: '# Photosynthesis: Nature\'s Energy Factory\n\n## What is Photosynthesis?\nPhotosynthesis is the process by which plants use sunlight, water, and carbon dioxide to create oxygen and energy in the form of glucose. This process is fundamental to life on Earth.\n\n## The Equation\n6CO₂ + 6H₂O + light energy → C₆H₁₂O₆ + 6O₂\n\n## Key Stages\n1. **Light-dependent reactions** - Occur in the thylakoid\n2. **Light-independent reactions** - Occur in the stroma\n\n## Learning Outcomes\n- Understand the overall process of photosynthesis\n- Identify the inputs and outputs\n- Explain the role of chlorophyll',
    tags: ['biology', 'photosynthesis', 'plants', 'energy'],
    createdAt: '2024-01-15'
  },
  {
    id: 'lesson-2',
    title: 'Quadratic Equations Mastery',
    subject: 'Mathematics',
    grade: 'Grade 10-11',
    teacher: 'John Smith',
    teacherId: 'user-t2',
    description: 'Complete guide to understanding and solving quadratic equations',
    duration: '60 min',
    difficulty: 'Intermediate',
    students: 67,
    rating: 4.9,
    thumbnail: 'https://images.unsplash.com/photo-1509228627152-72ae67a36bff?w=400&h=300&fit=crop',
    content: '# Mastering Quadratic Equations\n\n## Definition\nA quadratic equation is an equation of the form: ax² + bx + c = 0, where a ≠ 0\n\n## Methods of Solution\n1. **Factorization** - Breaking down into factors\n2. **Completing the Square** - Converting to perfect square\n3. **Quadratic Formula** - x = (-b ± √(b²-4ac)) / 2a\n\n## Applications\n- Projectile motion\n- Area problems\n- Profit maximization\n\n## Practice Problems\n- Solve: 2x² - 5x - 3 = 0\n- Solve: x² + 6x + 9 = 0\n- Find the vertex of: y = x² - 4x + 3',
    tags: ['mathematics', 'algebra', 'equations', 'formulas'],
    createdAt: '2024-01-20'
  },
  {
    id: 'lesson-3',
    title: 'Romeo and Juliet: A Timeless Tale',
    subject: 'English Literature',
    grade: 'Grade 10-11',
    teacher: 'Emily Davis',
    teacherId: 'user-t3',
    description: 'Deep dive into Shakespeare\'s most famous tragedy',
    duration: '90 min',
    difficulty: 'Intermediate',
    students: 54,
    rating: 4.7,
    thumbnail: 'https://images.unsplash.com/photo-1507842072343-583684e55f9c?w=400&h=300&fit=crop',
    content: '# Romeo and Juliet: Comprehensive Study Guide\n\n## Overview\nWritten by William Shakespeare, Romeo and Juliet is a tragedy about two star-crossed lovers from feuding families.\n\n## Key Themes\n1. **Love vs. Hate** - Central conflict of the play\n2. **Fate vs. Choice** - Is the tragedy predetermined?\n3. **Family Conflict** - The destructive nature of feuds\n\n## Main Characters\n- Romeo Montague\n- Juliet Capulet\n- Friar Lawrence\n- Mercutio\n- The Nurse\n\n## Major Acts Summary\n- Act 1: The Prologue and Meeting\n- Act 2: The Balcony Scene\n- Act 3: The Turning Point\n- Act 4: Desperation\n- Act 5: Tragedy',
    tags: ['literature', 'shakespeare', 'drama', 'classic'],
    createdAt: '2024-02-01'
  },
  {
    id: 'lesson-4',
    title: 'World War II: A Global Conflict',
    subject: 'History',
    grade: 'Grade 9-10',
    teacher: 'Sophia Anderson',
    teacherId: 'user-t5',
    description: 'Comprehensive overview of World War II and its impact on the world',
    duration: '75 min',
    difficulty: 'Intermediate',
    students: 38,
    rating: 4.6,
    thumbnail: 'https://images.unsplash.com/photo-1518693108963-fe03885a9bf4?w=400&h=300&fit=crop',
    content: '# World War II: Understanding the Global Conflict\n\n## Timeline\n- 1939: Germany invades Poland\n- 1941: Japan attacks Pearl Harbor\n- 1945: Germany and Japan surrender\n\n## Key Players\n- **Axis Powers**: Germany, Italy, Japan\n- **Allied Powers**: Britain, USA, Soviet Union\n\n## Major Events\n1. Blitzkrieg and Fall of France\n2. Battle of Britain\n3. Holocaust\n4. Pacific Theater\n5. D-Day and Liberation\n\n## Consequences\n- 70+ million deaths\n- Formation of United Nations\n- Cold War begins\n- Decolonization movements\n\n## Learning Resources\n- Primary documents\n- Documentary footage\n- Interactive timelines',
    tags: ['history', 'wwii', 'global-events', 'military'],
    createdAt: '2024-02-05'
  }
];

// ============================================================================
// DEMO ADMIN DATA
// ============================================================================

const DEMO_ADMIN_DATA = {
  dashboard: {
    totalUsers: 1847,
    totalTeachers: 287,
    totalStudents: 1423,
    totalAdmins: 12,
    activeUsers: 1447,
    userGrowth: 12,
    
    totalLessons: 487,
    lessonsCompleted: 3224,
    averageCompletionRate: 68,
    
    totalCourses: 145,
    totalDocuments: 2156,
    totalCoupons: 98,
    
    subscriptions: {
      free: 1023,
      premium: 567,
      enterprise: 12
    },
    
    engagementMetrics: {
      dailyActiveUsers: 845,
      weeklyActiveUsers: 1289,
      monthlyActiveUsers: 1567,
      averageSessionDuration: 45
    }
  },

  recentActivity: [
    { id: 1, user: 'Sarah Johnson', action: 'Completed lesson', lesson: 'Advanced Mathematics', time: '2 hours ago' },
    { id: 2, user: 'Michael Chen', action: 'Started lesson', lesson: 'Physics Fundamentals', time: '4 hours ago' },
    { id: 3, user: 'John Smith', action: 'Created lesson', lesson: 'Advanced Calculus', time: '1 day ago' },
    { id: 4, user: 'Emma Wilson', action: 'Completed lesson', lesson: 'English Literature', time: '1 day ago' },
    { id: 5, user: 'David Martinez', action: 'Updated lesson', lesson: 'Chemistry Basics', time: '2 days ago' }
  ],

  coupons: [
    { id: 1, code: 'WELCOME20', discount: '20%', uses: 134, limit: 500, active: true, createdAt: '2024-01-01' },
    { id: 2, code: 'SPRING2024', discount: '30%', uses: 89, limit: 300, active: true, createdAt: '2024-02-01' },
    { id: 3, code: 'STUDENT15', discount: '15%', uses: 245, limit: 1000, active: true, createdAt: '2024-01-15' },
    { id: 4, code: 'SUMMER50', discount: '50%', uses: 12, limit: 100, active: false, createdAt: '2024-03-01' },
    { id: 5, code: 'REFER10', discount: '10%', uses: 567, limit: 5000, active: true, createdAt: '2023-12-01' }
  ],

  documents: [
    { id: 1, name: 'Mathematics Guide 2024', type: 'PDF', size: '12.4 MB', uploads: 234, createdAt: '2024-01-10' },
    { id: 2, name: 'Science Curriculum', type: 'DOCX', size: '8.7 MB', uploads: 156, createdAt: '2024-01-15' },
    { id: 3, name: 'English Syllabus', type: 'PDF', size: '5.2 MB', uploads: 98, createdAt: '2024-01-20' },
    { id: 4, name: 'History Resources', type: 'ZIP', size: '45.8 MB', uploads: 67, createdAt: '2024-02-01' },
    { id: 5, name: 'Practice Tests', type: 'PDF', size: '22.1 MB', uploads: 342, createdAt: '2024-02-05' }
  ]
};

// ============================================================================
// DEMO FAQ DATA
// ============================================================================

const DEMO_FAQ = [
  {
    id: 'faq-1',
    question: 'How does photosynthesis work?',
    answer: 'Photosynthesis is a two-stage process where plants use light energy to convert CO₂ and water into glucose and oxygen. The light-dependent reactions occur in the thylakoid, while the light-independent reactions (Calvin cycle) occur in the stroma.',
    category: 'Biology',
    helpful: 145,
    unhelpful: 3
  },
  {
    id: 'faq-2',
    question: 'What is the quadratic formula?',
    answer: 'The quadratic formula is x = (-b ± √(b²-4ac)) / 2a. It\'s used to find the roots of any quadratic equation in the form ax² + bx + c = 0.',
    category: 'Mathematics',
    helpful: 234,
    unhelpful: 5
  },
  {
    id: 'faq-3',
    question: 'Why did Romeo and Juliet die?',
    answer: 'Romeo and Juliet died due to a series of misunderstandings and miscommunication. Romeo thought Juliet was dead and drank poison to join her, while Juliet woke to find Romeo actually dead and killed herself with his dagger.',
    category: 'Literature',
    helpful: 187,
    unhelpful: 8
  },
  {
    id: 'faq-4',
    question: 'What caused World War II?',
    answer: 'WWII was caused by multiple factors: Treaty of Versailles humiliation, economic depression, rise of fascism, and territorial aggression by Germany, Italy, and Japan.',
    category: 'History',
    helpful: 156,
    unhelpful: 4
  }
];

// ============================================================================
// DEMO CHAT HISTORY
// ============================================================================

const DEMO_CHAT_HISTORY = {
  'lesson-1': [
    { sender: 'student', message: 'What is photosynthesis?' },
    { sender: 'teacher', message: 'Photosynthesis is the process where plants convert sunlight, water, and CO₂ into glucose and oxygen. It\'s the foundation of life on Earth!' },
    { sender: 'student', message: 'Where does it happen in the plant?' },
    { sender: 'teacher', message: 'Great question! It happens primarily in the leaves, specifically in the chloroplasts. Chlorophyll captures the light energy.' },
    { sender: 'student', message: 'What about the two stages you mentioned?' },
    { sender: 'teacher', message: 'The light-dependent reactions happen in the thylakoid and produce ATP and NADPH. The light-independent reactions (Calvin cycle) happen in the stroma and use these molecules to make glucose.' }
  ],
  'lesson-2': [
    { sender: 'student', message: 'How do I solve quadratic equations?' },
    { sender: 'teacher', message: 'There are three main methods: factorization, completing the square, or using the quadratic formula. Which method are you most comfortable with?' },
    { sender: 'student', message: 'I know factorization but it doesn\'t always work' },
    { sender: 'teacher', message: 'Correct! That\'s why we have the quadratic formula. Let me show you: x = (-b ± √(b²-4ac)) / 2a. This always works for any quadratic equation.' },
    { sender: 'student', message: 'What\'s the discriminant?' },
    { sender: 'teacher', message: 'The discriminant is b²-4ac. It tells us about the nature of roots: positive = 2 real roots, zero = 1 real root, negative = 2 complex roots.' }
  ],
  'lesson-3': [
    { sender: 'student', message: 'What\'s the main theme of Romeo and Juliet?' },
    { sender: 'teacher', message: 'The main theme is love versus hate. The tragedy results from the feud between the Montagues and Capulets, showing how conflict destroys beauty.' },
    { sender: 'student', message: 'Was the ending inevitable?' },
    { sender: 'teacher', message: 'That\'s a great question! The prologue suggests fate, but Shakespeare also shows how choices and misunderstandings lead to tragedy. It\'s a mix of both.' },
    { sender: 'student', message: 'Why do you think it\'s still famous today?' },
    { sender: 'teacher', message: 'Because it explores timeless themes: forbidden love, family conflict, and the consequences of hatred. Every generation finds something relevant in it.' }
  ],
  'lesson-4': [
    { sender: 'student', message: 'When did World War II start?' },
    { sender: 'teacher', message: 'WWII began on September 1, 1939, when Germany invaded Poland. This triggered declarations of war from Britain and France.' },
    { sender: 'student', message: 'How many people died?' },
    { sender: 'teacher', message: 'Approximately 70-85 million people died, including the Holocaust where 6 million Jews were murdered. It was the deadliest conflict in history.' },
    { sender: 'student', message: 'What was the Pacific War about?' },
    { sender: 'teacher', message: 'Japan sought to establish a "Greater East Asia Co-Prosperity Sphere" through military expansion. This brought them into conflict with China, the USA, and other Allied nations.' }
  ]
};

// ============================================================================
// DEMO INITIALIZATION FUNCTION
// ============================================================================

/**
 * Initialize all demo data
 * This function loads demo data into the system for testing and demonstration
 */
function initializeDemoData() {
  if (!DEMO_CONFIG.DEMO_MODE_ENABLED) {
    console.log('📭 Demo mode is disabled');
    return;
  }

  if (DEMO_CONFIG.DEBUG_MODE) {
    console.log('🎬 Initializing demo data...');
  }

  // Store demo users in window object for easy access
  window.demoUsers = DEMO_USERS;
  window.demoLessons = DEMO_LESSONS;
  window.demoAdminData = DEMO_ADMIN_DATA;
  window.demoFAQ = DEMO_FAQ;
  window.demoChatHistory = DEMO_CHAT_HISTORY;

  // Initialize localStorage with demo data if needed
  if (DEMO_CONFIG.AUTO_LOAD_DEMO_DATA) {
    localStorage.setItem('demo_mode_enabled', 'true');
    localStorage.setItem('demo_lessons', JSON.stringify(DEMO_LESSONS));
    localStorage.setItem('demo_users', JSON.stringify(DEMO_USERS));
  }

  if (DEMO_CONFIG.DEBUG_MODE) {
    console.log('✅ Demo data initialized');
    console.log('📊 Available demo users:', DEMO_USERS.students.length, 'students,', DEMO_USERS.teachers.length, 'teachers,', DEMO_USERS.admins.length, 'admins');
    console.log('📚 Available lessons:', DEMO_LESSONS.length);
  }
}

/**
 * Get demo user by email
 */
function getDemoUserByEmail(email) {
  const allUsers = [
    ...DEMO_USERS.students,
    ...DEMO_USERS.teachers,
    ...DEMO_USERS.admins
  ];
  return allUsers.find(u => u.email === email);
}

/**
 * Get demo lessons by role
 */
function getDemoLessonsByRole(role) {
  if (role === 'student') {
    return DEMO_LESSONS; // Students can see all lessons
  } else if (role === 'teacher') {
    return DEMO_LESSONS; // Teachers can manage all lessons
  } else if (role === 'admin') {
    return DEMO_LESSONS; // Admins can see all lessons
  }
  return [];
}

/**
 * Get demo chat history for a lesson
 */
function getDemoChatHistory(lessonId) {
  return DEMO_CHAT_HISTORY[lessonId] || [];
}

/**
 * Get demo FAQ by category
 */
function getDemoFAQByCategory(category) {
  if (!category) return DEMO_FAQ;
  return DEMO_FAQ.filter(faq => faq.category === category);
}

// ============================================================================
// AUTO-INITIALIZATION
// ============================================================================

// Initialize demo data when this script loads
if (DEMO_CONFIG.AUTO_LOAD_DEMO_DATA) {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeDemoData);
  } else {
    initializeDemoData();
  }
}

// Log in as demo student
if (DEMO_CONFIG.DEMO_MODE_ENABLED) {
  const demoStudent = getDemoUserByEmail('student@iqbalai.com');
  if (demoStudent) {
    console.log(`🔑 Logged in as demo student: ${demoStudent.name}`);
    // Simulate browsing lessons
    const lessons = getDemoLessonsByRole('student');
    console.log('📚 Browsing lessons:', lessons.map(l => l.title).join(', '));
    
    // Simulate opening a lesson
    const lessonToOpen = lessons[0];
    console.log(`📖 Opening lesson: ${lessonToOpen.title}`);
    console.log('✅ Using demo lesson content:', lessonToOpen.title);
    
    // Load demo chat history for the lesson
    const chatHistory = getDemoChatHistory(lessonToOpen.id);
    console.log('✅ Demo chat history loaded for lesson:', lessonToOpen.id);
  }
}
