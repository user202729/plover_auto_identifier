def delete_char(context, content: str):
	action=context.new_action()
	action.prev_attach=True
	action.next_attach=True
	action.prev_replace=content
	action.text=""
	return action
