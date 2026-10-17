"""Vocabulary of technologies / methods used to compare a CV with a vacancy and to spot unverified skills."""

TECH = {
    # languages
    "python", "java", "javascript", "typescript", "go", "golang", "rust", "c++", "c#", "php", "ruby", "kotlin", "swift",
    "scala", "sql", "bash", "r", "dart", "elixir", "objective-c",
    # web / frameworks
    "django", "flask", "fastapi", "spring", "spring boot", "node.js", "express", "nestjs", "react", "next.js", "vue",
    "nuxt", "angular", "svelte", "redux", "react query", "tailwind", "graphql", "rest", "grpc", "websocket", "microservices", "laravel",
    "symfony", "rails", ".net", "asp.net", "flutter", "react native", "jquery", "storybook", "webpack", "vite",
    # data / ml
    "pandas", "numpy", "scikit-learn", "pytorch", "tensorflow", "keras", "hugging face", "transformers", "llm", "nlp",
    "computer vision", "machine learning", "opencv", "spark", "airflow", "dbt", "kafka", "rabbitmq", "celery", "mlflow", "kubeflow",
    "tableau", "power bi", "looker", "excel", "a/b testing", "statistics",
    # storage
    "postgresql", "mysql", "mongodb", "redis", "elasticsearch", "clickhouse", "snowflake", "bigquery", "dynamodb",
    "cassandra", "sqlite", "oracle",
    # infra
    "docker", "kubernetes", "k8s", "terraform", "ansible", "helm", "aws", "gcp", "azure", "linux", "nginx", "ci/cd",
    "github actions", "gitlab ci", "jenkins", "prometheus", "grafana", "sentry", "serverless", "lambda",
    # testing
    "pytest", "jest", "cypress", "playwright", "selenium", "testing library", "unit testing", "qa automation",
    # process / pm
    "agile", "scrum", "kanban", "jira", "confluence", "figma", "miro", "roadmap", "stakeholder management", "okr",
    "product discovery", "user research", "budgeting", "risk management", "pmp", "prince2", "safe",
}
WEAK_STARTS = (
    "responsible for", "worked on", "was involved", "involved in", "helped", "participated in", "duties included",
    "tasks included", "відповідав за", "відповідала за", "займався", "займалася", "брав участь", "брала участь",
    "допомагав", "допомагала", "мої обов",
)
CLICHES = (
    "hard-working", "hardworking", "team player", "stress-resistant", "stress resistant", "fast learner", "quick learner",
    "self-motivated", "detail-oriented", "results-driven", "go-getter", "think outside the box", "passionate about",
    "комунікабельн", "стресостійк", "відповідальн", "цілеспрямован", "швидко навчаюсь", "командний гравець",
)
SECTION_ALIASES = {
    "summary": ("summary", "profile", "about", "objective", "про себе", "профіль", "резюме", "коротко"),
    "experience": ("experience", "employment", "work history", "career", "досвід", "досвід роботи", "робота"),
    "skills": ("skills", "technologies", "tech stack", "competencies", "навички", "технології", "стек"),
    "education": ("education", "university", "degree", "освіта"),
    "languages": ("languages", "мови", "english"),
}
ROLE_WORDS = ("developer", "engineer", "manager", "analyst", "designer", "lead", "intern", "specialist", "consultant",
              "architect", "scientist", "administrator", "officer", "director", "head", "owner", "coordinator", "tester",
              "qa", "розробник", "розробниця", "менеджер", "аналітик", "дизайнер", "інженер", "керівник", "стажер",
              "консультант", "тестувальник")
