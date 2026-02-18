/**
 * Backend Response Formatter
 * This file demonstrates how a backend should format responses in Markdown
 * Can be used with Node.js/Express or adapted for other backends
 * 
 * Example usage in Express:
 * app.post('/api/chat', (req, res) => {
 *   const response = BackendResponseFormatter.formatResponse(req.body.query);
 *   res.json({ message: response });
 * });
 */

const BackendResponseFormatter = (() => {
  /**
   * Format AI responses in Markdown with tables, headings, and structured content
   * @param {string} query - User query
   * @returns {string} Markdown formatted response
   */
  function formatResponse(query) {
    const query_lower = query.toLowerCase();

    // Example 1: Learning outcomes response with table
    if (query_lower.includes('learning') || query_lower.includes('objectives')) {
      return `# Learning Outcomes

## For the Photosynthesis Lesson

| Learning Level | Objective | Assessment Method |
|---|---|---|
| Remember | Define photosynthesis | Quiz |
| Understand | Explain the process | Discussion |
| Apply | Conduct experiments | Lab Report |
| Analyze | Compare C3 and C4 | Research Paper |
| Evaluate | Design efficient systems | Project |

### Key Takeaways
- Students will understand the importance of photosynthesis
- They will be able to explain the light and dark reactions
- Practical application through laboratory work`;
    }

    // Example 2: Assessment guide with structured content
    if (query_lower.includes('assessment') || query_lower.includes('quiz')) {
      return `# Assessment Strategy

## Multiple Choice Questions

### Question 1
**What is the primary function of chlorophyll?**
- a) Energy storage
- b) Light absorption
- c) Protein synthesis
- d) Oxygen production

### Question 2
**Where does the light reaction occur?**
- a) Stroma
- b) Thylakoid
- c) Mitochondria
- d) Nucleus

## Rubric Table

| Criteria | Excellent (4) | Good (3) | Fair (2) | Poor (1) |
|---|---|---|---|---|
| **Understanding** | Complete grasp of concepts | Mostly understands | Some understanding | Little understanding |
| **Application** | Applies to new contexts | Applies correctly | Limited application | Cannot apply |
| **Communication** | Clear and organized | Generally clear | Somewhat clear | Unclear |

### Scoring Guide
- 12-15: Excellent Mastery (A)
- 9-11: Good Understanding (B)
- 6-8: Fair Comprehension (C)`;
    }

    // Example 3: Lesson plan with proper formatting
    if (query_lower.includes('lesson') || query_lower.includes('plan')) {
      return `# Comprehensive Lesson Plan

## Title: Introduction to Cell Division

### Grade Level: 9-10
### Duration: 45 minutes
### Subject: Biology

## Objectives

### Students will be able to:
- Identify the phases of mitosis
- Explain the purpose of cell division
- Observe cell division under microscope

## Materials Required

| Item | Quantity | Notes |
|---|---|---|
| Microscopes | 1 per group | Working condition |
| Prepared slides | 3-4 sets | Different phases |
| Observation sheets | 1 per student | Printed |
| Colored markers | 1 set | For diagrams |

## Lesson Timeline

1. **Introduction (5 min)**
   - Review cell structure
   - Introduce cell division importance

2. **Direct Instruction (10 min)**
   - Explain mitosis phases
   - Show diagrams and animations

3. **Guided Practice (15 min)**
   - Microscope observation
   - Identify cell phases

4. **Independent Practice (10 min)**
   - Complete observation sheet
   - Draw and label diagrams

5. **Closure (5 min)**
   - Discuss findings
   - Preview next lesson

### Assessment Strategies

| Strategy | When | Tools |
|---|---|---|
| Observation | During practice | Checklist |
| Work samples | Completion | Rubric |
| Exit ticket | End | Quick questions |`;
    }

    // Example 4: Study guide with headers and lists
    if (query_lower.includes('study') || query_lower.includes('prepare')) {
      return `# Study Guide: Ecosystems

## Chapter Summary

### What is an Ecosystem?
An ecosystem is a community of living organisms interacting with their physical environment.

### Key Components

#### Biotic Factors
- Living organisms
- Plants and animals
- Decomposers

#### Abiotic Factors
- Temperature
- Light
- Water
- Soil

## Energy Flow

### Food Chains
1. Producers (plants)
2. Primary consumers (herbivores)
3. Secondary consumers (carnivores)
4. Decomposers

### Energy Pyramid

| Level | Organisms | Energy Available |
|---|---|---|
| Producers | Plants | 100% |
| Primary Consumers | Herbivores | 10% |
| Secondary Consumers | Carnivores | 1% |

## Practice Questions

- What distinguishes a food web from a food chain?
- How does energy flow through an ecosystem?
- Give examples of biotic and abiotic factors`;
    }

    // Default response with tables and formatting
    return `# Teaching Assistant Response

## Response Overview

This is a structured markdown response demonstrating proper formatting.

### Key Points

- **Point 1**: Educational best practices
- **Point 2**: Student engagement strategies  
- **Point 3**: Assessment techniques

## Comparison Table

| Approach | Benefits | Challenges |
|---|---|---|
| Lecture | Efficient coverage | Passive learning |
| Discussion | Active engagement | Time consuming |
| Hands-on | Deep learning | Resource intensive |

### Recommendations

1. Start with clear learning objectives
2. Use diverse teaching methods
3. Regular formative assessment
4. Provide constructive feedback

\`\`\`
Example code or content here
\`\`\`

Would you like me to elaborate on any of these points?`;
  }

  /**
   * Format error response in Markdown
   */
  function formatError(error) {
    return `# Error Processing Request

## Details
- **Error Type**: ${error.type || 'Unknown'}
- **Message**: ${error.message || 'An unexpected error occurred'}

### Next Steps
1. Check your input
2. Verify connection
3. Try again

**Support**: Contact your system administrator if the problem persists.`;
  }

  /**
   * Format data table response
   */
  function formatTable(headers, rows, title = 'Data Table') {
    let markdown = `# ${title}\n\n`;
    
    // Create header row
    markdown += '| ' + headers.join(' | ') + ' |\n';
    markdown += '|' + headers.map(() => '---|').join('') + '\n';
    
    // Create data rows
    rows.forEach(row => {
      markdown += '| ' + row.join(' | ') + ' |\n';
    });
    
    return markdown;
  }

  return {
    formatResponse,
    formatError,
    formatTable
  };
})();

// Example usage in backend (Node.js):
// const express = require('express');
// const app = express();
//
// app.post('/api/generate-response', (req, res) => {
//   const { query } = req.body;
//   const markdownResponse = BackendResponseFormatter.formatResponse(query);
//   res.json({
//     success: true,
//     message: markdownResponse,
//     format: 'markdown'
//   });
// });

// Export for Node.js
if (typeof module !== 'undefined' && module.exports) {
  module.exports = BackendResponseFormatter;
}
