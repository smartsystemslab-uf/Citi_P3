# Citi-Bank-P3
This repository contains the main components of the Citi Banking P3 project. The project focuses on building an agentic AI banking system, backend banking services, MCP-based tool integration, and MLflow-based observability for monitoring agent behavior.

## Developer Guidelines:
1. For every script you add here, please make sure you have ensure there is 100% unit test coverage. It's no need to post your unit test code here, but in the future, if our project is large enough as an industry-level project, unit test code is required to be committed to the repository. 
Learn more about unit test here: https://www.ibm.com/think/topics/unit-testing.

2. Every `.py` file or other executable file added to this project must also have corresponding integration tests codes included in the repository. All integration tests must pass before submission. 
Learn more about integration test here: https://circleci.com/blog/unit-testing-vs-integration-testing/

3. The use of AI tools and other unit testing tools is encouraged. Please use whichever tools work best for your workflow.

4. When pushing changes to the repository, please include a brief description of what was changed and why the change was necessary.

---

## Viewer Guidelines:
Each folder is an independent module with its own internal documentation and deployment steps.  
Please go into the corresponding folder and follow its local README or setup guide for detailed installation and execution instructions.

---

### 1. `citi_banking_adk/`

This folder contains the main agentic banking system built with Google ADK.

It includes the core multi-agent workflows for banking operations, such as:

- Front-office customer interaction
- Transaction handling
- Fraud or suspicious activity routing
- Back-office workflow coordination
- End-of-day processing
- Agent-to-agent orchestration

This module is responsible for running the banking agents and coordinating the overall agent workflow.

For detailed setup, environment configuration, and execution commands, please check the documentation inside this folder.

---

### 2. `citi_banking_adk_mlflow_observability/`

This folder contains the MLflow-based observability system for the banking agents.

It is used to monitor and analyze agent behavior during execution, including:

- Agent traces
- Tool calls
- Intermediate decisions
- Input and output logs
- Runtime behavior
- Metrics and experiment records

The purpose of this module is to provide visibility into how the agents work, how they make decisions, and how the system can be audited or evaluated.

For detailed deployment and usage steps, please follow the instructions inside this folder.

---

### 3. `citi_banking_backend/`

This folder contains the backend banking API and database-related services.

It provides the backend support needed by the agentic banking system, such as:

- Customer information
- Account data
- Transaction data
- Banking operation APIs
- Backend service endpoints
- Database interaction logic

The agent system can call this backend to retrieve banking context and execute structured banking operations.

For database setup, API deployment, and testing instructions, please refer to the documentation inside this folder.

---

### 4. `citi_banking_mcp_server/`

This folder contains the MCP server implementation.

The MCP server provides a standardized way for agents to access external tools and backend services.  
It acts as a bridge between the agent system and the banking backend or other callable tools.

This module is used for:

- Tool registration
- MCP-based communication
- Backend tool exposure
- Agent-to-tool interaction
- Standardized service access

For detailed MCP server setup and deployment steps, please check the instructions inside this folder.
