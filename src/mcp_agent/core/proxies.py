"""
Proxy classes for agent interactions.
These proxies provide a consistent interface for interacting with different types of agents.
"""

from typing import List, Optional, Dict, TYPE_CHECKING

from mcp_agent.agents.agent import Agent
from mcp_agent.app import MCPApp

# Handle circular imports
if TYPE_CHECKING:
    from mcp_agent.core.types import WorkflowType, ProxyDict
else:
    # Define minimal versions for runtime
    from typing import Any

    # Use Any for runtime to avoid circular imports
    WorkflowType = Any
    ProxyDict = Dict[str, "BaseAgentProxy"]


class BaseAgentProxy:
    """Base class for all proxy types"""

    def __init__(self, app: MCPApp, name: str):
        self._app = app
        self._name = name

    async def __call__(self, message: Optional[str] = None) -> str:
        """Allow: agent.researcher('message')"""
        return await self.send(message)

    async def send(self, message: Optional[str] = None) -> str:
        """Allow: agent.researcher.send('message')"""
        if message is None:
            return await self.prompt()
        return await self.generate_str(message)

    async def prompt(self, default_prompt: str = "") -> str:
        """Allow: agent.researcher.prompt()"""
        return await self._app.prompt(self._name, default_prompt)

    async def generate_str(self, message: str) -> str:
        """Generate response for a message - must be implemented by subclasses"""
        raise NotImplementedError("Subclasses must implement generate_str")

    async def load_prompt(self, prompt_name: str = None, arguments: dict[str, str] = None) -> str:
        """
        Use a Prompt from an MCP Server - implemented by subclasses.
        Always returns an Assistant message.
        
        Args:
            prompt_name: Name of the prompt to load
            arguments: Optional dictionary of string arguments for prompt templating
        """
        raise NotImplementedError("Subclasses must implement mcp-prompt")


class LLMAgentProxy(BaseAgentProxy):
    """Proxy for regular agents that use _llm.generate_str()"""

    def __init__(self, app: MCPApp, name: str, agent: Agent):
        super().__init__(app, name)
        self._agent = agent

    async def generate_str(self, message: str, **kwargs) -> str:
        """Forward message and all kwargs to the agent's LLM"""
        return await self._agent._llm.generate_str(message, **kwargs)

    async def load_prompt(self, prompt_name: str = None, arguments: dict[str, str] = None) -> str:
        """
        Load and apply a prompt from an MCP server.
        
        Args:
            prompt_name: Name of the prompt to load
            arguments: Optional dictionary of string arguments for prompt templating
            
        Returns:
            The assistant's response
        """
        return await self._agent.load_prompt(prompt_name, arguments)


class WorkflowProxy(BaseAgentProxy):
    """Proxy for workflow types that implement generate_str() directly"""

    def __init__(self, app: MCPApp, name: str, workflow: WorkflowType):
        super().__init__(app, name)
        self._workflow = workflow

    async def generate_str(self, message: str, **kwargs) -> str:
        """Forward message and all kwargs to the underlying workflow"""
        return await self._workflow.generate_str(message, **kwargs)


class RouterProxy(BaseAgentProxy):
    """Proxy for LLM Routers"""

    def __init__(self, app: MCPApp, name: str, workflow: WorkflowType):
        super().__init__(app, name)
        self._workflow = workflow

    async def generate_str(self, message: str, **kwargs) -> str:
        """
        Route the message and forward kwargs to the resulting agent if applicable.
        Note: For now, route() itself doesn't accept kwargs.
        """
        results = await self._workflow.route(message)
        if not results:
            return "No appropriate route found for the request."

        # Get the top result
        top_result = results[0]
        if isinstance(top_result.result, Agent):
            # Agent route - delegate to the agent, passing along kwargs
            agent = top_result.result
            return await agent._llm.generate_str(message, **kwargs)
        elif isinstance(top_result.result, str):
            # Server route - use the router directly
            return "Tool call requested by router - not yet supported"

        return f"Routed to: {top_result.result} ({top_result.confidence}): {top_result.reasoning}"


class ChainProxy(BaseAgentProxy):
    """Proxy for chained agent operations"""

    def __init__(
        self, app: MCPApp, name: str, sequence: List[str], agent_proxies: ProxyDict
    ):
        super().__init__(app, name)
        self._sequence = sequence
        self._agent_proxies = agent_proxies
        self._continue_with_final = True  # Default behavior
        self._cumulative = False  # Default to sequential chaining

    async def generate_str(self, message: str, **kwargs) -> str:
        """Chain message through a sequence of agents.

        For the first agent in the chain, pass all kwargs to maintain transparency.

        Two modes of operation:
        1. Sequential (default): Each agent receives only the output of the previous agent
        2. Cumulative: Each agent receives all previous agent responses concatenated
        """
        if not self._sequence:
            return message

        # Process the first agent (same for both modes)
        first_agent = self._sequence[0]
        first_proxy = self._agent_proxies[first_agent]
        first_response = await first_proxy.generate_str(message, **kwargs)

        if len(self._sequence) == 1:
            return first_response

        if self._cumulative:
            # Cumulative mode: each agent gets all previous responses
            cumulative_response = f'<fastagent:response agent="{first_agent}">\n{first_response}\n</fastagent:response>'

            # Process subsequent agents with cumulative results
            for agent_name in self._sequence[1:]:
                proxy = self._agent_proxies[agent_name]
                # Pass all previous responses to next agent
                agent_response = await proxy.generate_str(cumulative_response)
                # Add this agent's response to the cumulative result
                cumulative_response += f'\n\n<fastagent:response agent="{agent_name}">\n{agent_response}\n</fastagent:response>'

            return cumulative_response
        else:
            # Sequential chaining (original behavior)
            current_message = first_response

            # For subsequent agents, just pass the message from previous agent
            for agent_name in self._sequence[1:]:
                proxy = self._agent_proxies[agent_name]
                current_message = await proxy.generate_str(current_message)

            return current_message
