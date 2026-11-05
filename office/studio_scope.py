def supports_studio(workspace):
    """Enable the 3D office per workspace, independently of its AI provider."""
    return isinstance(workspace.policy, dict) and workspace.policy.get('claw3d_studio') is True
