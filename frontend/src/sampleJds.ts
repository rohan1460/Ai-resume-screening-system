/**
 * Sample job descriptions, one per common role.
 *
 * Written against the shipped skills dictionary (`config/skills.txt`) so a sample
 * actually produces a full set of required skills — a demo JD whose terms the
 * matcher does not know would make the product look broken rather than the sample.
 */

export interface SampleJd {
  id: string;
  label: string;
  /** Shown under the label in the menu. */
  hint: string;
  title: string;
  text: string;
}

export const SAMPLE_JDS: SampleJd[] = [
  {
    id: 'backend',
    label: 'Backend Engineer',
    hint: 'Python · FastAPI · PostgreSQL · Celery',
    title: 'Senior Backend Engineer',
    text: `Senior Backend Engineer

We are looking for a Senior Backend Engineer to design and build the services behind
our hiring platform.

Requirements:
- Strong Python experience, ideally with FastAPI or Django
- Solid SQL and PostgreSQL knowledge
- Experience with Redis and Celery for async task processing
- Docker and Kubernetes in production
- Familiarity with AWS
- Good testing discipline with pytest
- Comfortable with System Design and Distributed Systems

Nice to have:
- Machine Learning or NLP exposure
- Experience with Elasticsearch`,
  },
  {
    id: 'frontend',
    label: 'Frontend Engineer',
    hint: 'React · TypeScript · Next.js · CSS',
    title: 'Senior Frontend Engineer',
    text: `Senior Frontend Engineer

We are building the interface our recruiters live in all day, and we want someone who
cares about how it feels to use.

Requirements:
- Strong JavaScript and TypeScript
- Deep React experience; Next.js is a plus
- Confident with HTML, CSS and Tailwind CSS
- State management with Redux or equivalent
- Build tooling: Vite or Webpack
- Testing with Jest and either Cypress or Playwright
- Comfortable with REST API and GraphQL integration
- Git and Code Review as daily habits

Nice to have:
- Accessibility Testing experience
- Figma fluency for working with designers`,
  },
  {
    id: 'fullstack',
    label: 'Full Stack Engineer',
    hint: 'React · Node.js · PostgreSQL · AWS',
    title: 'Full Stack Engineer',
    text: `Full Stack Engineer

You will own features end to end, from the database through the API to the screen.

Requirements:
- TypeScript and JavaScript across the stack
- React on the front end, Node.js and Express.js on the back
- Python is welcome as a second backend language
- PostgreSQL and solid SQL
- REST API design; GraphQL a plus
- Docker, CI/CD and deployment on AWS
- Testing with Jest and pytest
- Git, Agile and Code Review

Nice to have:
- Redis for caching
- Kubernetes exposure
- System Design experience`,
  },
  {
    id: 'ai',
    label: 'AI / ML Engineer',
    hint: 'PyTorch · NLP · LangChain · RAG',
    title: 'AI / Machine Learning Engineer',
    text: `AI / Machine Learning Engineer

We are putting language models into production and need someone who has done it before,
not only in a notebook.

Requirements:
- Strong Python, with pandas and NumPy
- Machine Learning and Deep Learning fundamentals
- PyTorch or TensorFlow in production
- Natural Language Processing, with spaCy or Hugging Face Transformers
- Working with LLM systems: RAG, LangChain, Embeddings
- Vector Database experience (pgvector, Pinecone or Weaviate)
- MLOps and Model Deployment
- Docker and AWS
- Testing with pytest

Nice to have:
- scikit-learn and XGBoost for classical baselines
- Apache Spark or Airflow for data pipelines
- Computer Vision exposure`,
  },
  {
    id: 'qa',
    label: 'QA Engineer',
    hint: 'Selenium · Cypress · API Testing',
    title: 'QA Automation Engineer',
    text: `QA Automation Engineer

You will own the quality of a platform recruiters make hiring decisions on, so the bar
for correctness is high.

Requirements:
- Test Automation with Selenium, Cypress or Playwright
- Strong API Testing, with Postman and Swagger
- Regression Testing and End to End Testing suites
- Test Planning, Test Cases and Bug Tracking in Jira
- Python or JavaScript for writing tests
- pytest, JUnit or TestNG
- Performance Testing with JMeter
- CI/CD pipelines with GitHub Actions or Jenkins
- SQL for verifying data
- Attention to Detail and clear Communication

Nice to have:
- Behaviour Driven Development with Cucumber
- Mobile Testing with Appium
- Accessibility Testing and Cross Browser Testing`,
  },
];
