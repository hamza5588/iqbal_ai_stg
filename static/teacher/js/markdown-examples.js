/**
 * Markdown Integration Examples for Iqbal AI
 * Shows how to use the Markdown parser in real scenarios
 */

// ==============================================================
// EXAMPLE 1: Simple Markdown Response
// ==============================================================
function exampleSimpleMarkdown() {
  const markdownContent = `
# Welcome to Markdown Rendering

## This is a Section

Here's some information with **bold** and *italic* text.

### Subsection

- Point 1
- Point 2
- Point 3
  `;
  
  // Use in chat
  addAssistantMessage(MarkdownParser.parse(markdownContent));
}

// ==============================================================
// EXAMPLE 2: Table for Learning Outcomes
// ==============================================================
function exampleLearningOutcomesTable() {
  const tableMarkdown = `
# Learning Outcomes for Photosynthesis

## Bloom's Taxonomy Alignment

| Level | Objective | Activity | Assessment |
|-------|-----------|----------|------------|
| **Remember** | Define photosynthesis | Lecture with slides | Multiple choice quiz |
| **Understand** | Explain light reactions | Video animation | Short answer |
| **Apply** | Use equations in problems | Problem sets | Homework |
| **Analyze** | Compare C3 and C4 pathways | Research activity | Comparison chart |
| **Evaluate** | Judge efficiency of systems | Lab experiment | Lab report |
| **Create** | Design artificial system | Project | Final presentation |

### Key Takeaways
- All levels of understanding covered
- Mix of assessments ensures depth
- Hands-on activities promote engagement
  `;
  
  addAssistantMessage(MarkdownParser.parse(tableMarkdown));
}

// ==============================================================
// EXAMPLE 3: Structured Quiz with Tables
// ==============================================================
function exampleQuizWithTable() {
  const quizMarkdown = `
# Biology Quiz: Cell Division

## Section 1: Multiple Choice

1. **What is the primary purpose of mitosis?**
   - A) Producing sex cells
   - B) Creating genetically identical cells
   - C) Reducing chromosome number
   - D) Producing proteins

2. **During which phase do chromosomes align at the metaphase plate?**
   - A) Prophase
   - B) Metaphase
   - C) Anaphase
   - D) Telophase

## Section 2: Answer Key

| Question | Answer | Points | Difficulty |
|----------|--------|--------|------------|
| 1 | B | 5 | Easy |
| 2 | B | 5 | Medium |
| 3 | A | 5 | Hard |
| 4 | C | 5 | Medium |
| 5 | B | 5 | Easy |

### Scoring Guide
- 25 points: Excellent Mastery (A)
- 20-24 points: Good Understanding (B)
- 15-19 points: Fair Comprehension (C)
- Below 15: Needs Review

Total Points: 25
  `;
  
  addAssistantMessage(MarkdownParser.parse(quizMarkdown));
}

// ==============================================================
// EXAMPLE 4: Lesson Plan with Multiple Tables
// ==============================================================
function exampleComprehensiveLessonPlan() {
  const lessonMarkdown = `
# Comprehensive Lesson Plan: Introduction to Ecosystems

## Course Information
- **Subject**: Biology
- **Grade Level**: 9-10
- **Duration**: 45 minutes
- **Materials**: Microscopes, organisms, habitat samples

## Learning Objectives

### Students will be able to:
1. Define ecosystem and identify components
2. Classify organisms by trophic levels
3. Explain energy flow through food chains
4. Analyze ecosystem relationships

## Materials & Equipment

| Item | Quantity | Notes |
|------|----------|-------|
| Microscopes | 1 per pair | Must be functioning |
| Prepared slides | 5 sets | Different organisms |
| Observation sheets | 1 per student | For note-taking |
| Markers | 1 set per group | For diagrams |
| Field guide | 1 per group | Species identification |

## Lesson Timeline

### Introduction (5 minutes)
- Hook: "What would happen if all insects disappeared?"
- Connect to prior knowledge about food chains
- Preview learning objectives

### Instruction (12 minutes)
1. Define ecosystem (2 min)
2. Explain biotic/abiotic factors (4 min)
3. Demonstrate food web (4 min)
4. Show microscope techniques (2 min)

### Guided Practice (15 minutes)
- Microscope observations in pairs
- Students identify organisms
- Record observations on sheets

### Independent Practice (10 minutes)
- Complete ecosystem analysis worksheet
- Draw personal food web examples
- Compare findings with partner

### Closure (3 minutes)
- Discuss key findings
- Preview next lesson on populations

## Assessment Strategies

| Strategy | When | Tools | Rubric |
|----------|------|-------|--------|
| Observation | During activity | Checklist | Complete/Incomplete |
| Work sample | During practice | Rubric | 1-4 scale |
| Exit ticket | End of class | Questions | 0-5 points |
| Homework | After class | Worksheet | Correct answers |

## Differentiation

### For Advanced Learners
- Analyze multi-level food webs
- Research ecosystem disruption
- Study invasive species

### For Struggling Learners
- Simplified vocabulary cards
- Pair with advanced student
- Extra practice with diagrams

## Technology Integration

\`\`\`
- Projector for images
- Document camera for specimens
- Interactive ecosystem diagram
- Video on energy pyramid
\`\`\`

## Resources & References

- Biology textbook Chapter 5
- National Geographic ecosystem videos
- Interactive food web simulator
- Field guides for local species

## Post-Lesson Reflection

### What worked well:
- Hands-on microscope activity
- Student engagement with topic
- Pacing allowed for all students

### What needs improvement:
- More time for detailed observations
- Additional examples of ecosystems
- Better connection to local environment

---

**Prepared by**: Teacher Name  
**Date**: February 5, 2026  
**Approved by**: Department Head
  `;
  
  addAssistantMessage(MarkdownParser.parse(lessonMarkdown));
}

// ==============================================================
// EXAMPLE 5: Assessment Rubric Table
// ==============================================================
function exampleAssessmentRubric() {
  const rubricMarkdown = `
# Project Assessment Rubric

## Creative Writing Submission

| Criteria | Excellent (4) | Good (3) | Fair (2) | Poor (1) | Score |
|----------|---|---|---|---|---|
| **Content & Ideas** | Engaging, original ideas throughout | Mostly engaging with some originality | Some interesting ideas | Minimal or unclear ideas |  |
| **Organization** | Clear structure, logical flow | Generally organized, minor issues | Some organization present | Disorganized |  |
| **Grammar & Spelling** | Few or no errors | Minor errors | Several errors | Many errors |  |
| **Creativity** | Highly creative | Creative elements | Some creativity | Lacks creativity |  |
| **Length** | Exceeds requirement | Meets requirement | Slightly under | Significantly under |  |
| **Mechanics** | Excellent punctuation | Mostly correct | Several issues | Many issues |  |

### Scoring Instructions
- **18-24**: A (Excellent)
- **15-17**: B (Good)
- **12-14**: C (Fair)
- **9-11**: D (Poor)
- **Below 9**: F (Unsatisfactory)

### Feedback Space

\`\`\`
Strengths:
[Provide specific positive feedback]

Areas for Improvement:
[Suggest specific areas to work on]

Next Steps:
[Recommend actions for next submission]
\`\`\`
  `;
  
  addAssistantMessage(MarkdownParser.parse(rubricMarkdown));
}

// ==============================================================
// EXAMPLE 6: Dynamic Markdown from Backend Response
// ==============================================================
function handleBackendMarkdownResponse(response) {
  // Assuming response.message is Markdown from backend
  const markdownContent = response.message;
  
  // Parse and display
  const htmlContent = MarkdownParser.parse(markdownContent);
  addAssistantMessage(htmlContent);
}

// Example usage:
function fetchAndDisplayMarkdown(query) {
  // Simulate API call
  fetch('/api/generate-response', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query })
  })
  .then(res => res.json())
  .then(data => {
    if (data.format === 'markdown') {
      const parsed = MarkdownParser.parse(data.message);
      addAssistantMessage(parsed);
    }
  });
}

// ==============================================================
// EXAMPLE 7: Combined Chat with Markdown
// ==============================================================
function exampleChatWithMarkdown() {
  // User sends message
  const userQuery = "Create a study guide for photosynthesis";
  addUserMessage(userQuery);
  
  // Simulate AI response in Markdown
  setTimeout(() => {
    const markdownResponse = `
# Study Guide: Photosynthesis

## Definition
Photosynthesis is the process by which plants convert light energy into chemical energy.

## Key Equation

\`\`\`
6CO₂ + 6H₂O + Light Energy → C₆H₁₂O₆ + 6O₂
\`\`\`

## Two Main Stages

### 1. Light-Dependent Reactions
- Location: Thylakoid membranes
- Inputs: Water, light
- Outputs: ATP, NADPH, oxygen

### 2. Light-Independent Reactions (Calvin Cycle)
- Location: Stroma
- Inputs: CO₂, ATP, NADPH
- Outputs: Glucose

## Important Terms

| Term | Definition |
|------|-----------|
| Chloroplast | Organelle where photosynthesis occurs |
| Chlorophyll | Pigment that absorbs light |
| Stroma | Fluid-filled space in chloroplast |
| Thylakoid | Disc-shaped structure in chloroplast |
| ATP | Energy molecule |

## Practice Questions

1. Where does the light reaction occur?
   - Answer: Thylakoid membranes

2. What is the primary product of photosynthesis?
   - Answer: Glucose (and oxygen)

3. What pigment is responsible for light absorption?
   - Answer: Chlorophyll

## Study Tips
- Create flashcards for vocabulary
- Draw diagrams of both stages
- Practice the photosynthesis equation
- Watch video animations
- Join study group for discussions
    `;
    
    addAssistantMessage(MarkdownParser.parse(markdownResponse));
  }, 1000);
}

// ==============================================================
// EXAMPLE 8: Safe User Input Handling
// ==============================================================
function exampleSafeUserInput(userContent) {
  // Always sanitize user input before displaying
  const safeContent = MarkdownParser.sanitizeHtml(userContent);
  const parsed = MarkdownParser.parse(safeContent);
  addAssistantMessage(parsed);
}

// ==============================================================
// EXAMPLE 9: Create Dynamic Lesson Content
// ==============================================================
function createDynamicLessonMarkdown(lessonData) {
  const { title, subject, gradeLevel, objectives, activities, assessment } = lessonData;
  
  const markdown = `
# ${title}

## Course Info
- **Subject**: ${subject}
- **Grade**: ${gradeLevel}

## Learning Objectives

${objectives.map((obj, i) => `${i + 1}. ${obj}`).join('\n')}

## Activities

| Activity | Duration | Materials |
|----------|----------|-----------|
${activities.map(act => `| ${act.name} | ${act.duration} | ${act.materials} |`).join('\n')}

## Assessment

${assessment}
  `;
  
  return MarkdownParser.parse(markdown);
}

// Usage:
const lessonData = {
  title: 'Algebra Fundamentals',
  subject: 'Mathematics',
  gradeLevel: '7-8',
  objectives: [
    'Solve linear equations',
    'Understand variable concepts',
    'Apply algebra to real problems'
  ],
  activities: [
    { name: 'Equation solving practice', duration: '15 min', materials: 'Worksheets' },
    { name: 'Group problem solving', duration: '20 min', materials: 'Whiteboards' },
    { name: 'Real-world application', duration: '10 min', materials: 'Word problems' }
  ],
  assessment: 'Quiz on solving linear equations'
};

// ==============================================================
// UTILITY FUNCTIONS
// ==============================================================

// Show Markdown parsing in real time
function demonstrateMarkdownParsing() {
  console.log('=== Markdown Parser Demo ===');
  
  const samples = [
    { name: 'Heading', md: '# Main Title' },
    { name: 'Table', md: '| A | B |\n|---|---|\n| 1 | 2 |' },
    { name: 'List', md: '- Item 1\n- Item 2' },
    { name: 'Code', md: '`const x = 5;`' },
    { name: 'Bold', md: '**Important**' }
  ];
  
  samples.forEach(sample => {
    const html = MarkdownParser.parse(sample.md);
    console.log(`${sample.name}: ${html}`);
  });
}

// Validate Markdown before sending
function validateMarkdownContent(content) {
  try {
    const html = MarkdownParser.parse(content);
    return {
      valid: true,
      html,
      message: 'Markdown parsed successfully'
    };
  } catch (error) {
    return {
      valid: false,
      error: error.message,
      message: 'Markdown parsing failed'
    };
  }
}

// ==============================================================
// EXPORT FOR USE IN DASHBOARDS
// ==============================================================

// These functions are now available for use throughout the application
// They can be called directly or integrated into existing chat systems
