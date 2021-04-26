from typing import Dict, Tuple, TYPE_CHECKING, NamedTuple, List, MutableMapping, Sequence, Optional
from collections import defaultdict
import sys
import re
import json
from pathlib import Path
import argparse
import functools

from plover.oslayer.config import CONFIG_DIR
from plover import log
from plover_build_utils.testing import CaptureOutput
from plover.translation import Translation, Translator
from plover.formatting import Formatter
from plover.steno import Stroke

if TYPE_CHECKING:
	import plover.engine

#stored_wordlist=Path(CONFIG_DIR)/"wordlist.json"
stored_wordlist=Path("/tmp/wordlist.v3.json")

L=print
#try:
#	logfile=Path("/tmp/L").open("w", buffering=1)
#	L=functools.partial(print, file=logfile)
#	# could use log.debug too but there's no way to turn off Plover's debug
#except:
#	pass


@functools.lru_cache()
def to_simple(word: str)->str:
	return re.sub(r"\W|_", "", word).lower()

def effective_no_op(word: str)->bool:
	return to_simple(word)==""

def lower_first_character(string: str)->str:
	if not string: return string
	return string[0].lower()+string[1:]

@functools.lru_cache()
def translations_to_output_1(translations: Tuple[str], initial_text: str="")->CaptureOutput:
	# Translation object can never contain macro info

	# The list should not contain replaced entries
	# so for example, [Translation(A, replaced=[Translation(B)])] is okay
	# while [Translation(B), Translation(A, replaced=[Translation(B)])] is not

	# at the moment, each element of `translations` is a `.english` translation result

	# the result (text, instructions) should not be modified,
	# otherwise the caching will return wrong result.

	# the formatter might raise some error

	output = CaptureOutput()
	formatter = Formatter(); formatter.set_output(output)
	formatter.start_attached = True
	formatter.spaces_after = False
	formatter.start_capitalized = False

	output.text = initial_text

	fake_strokes=[Stroke([])]

	if translations:
		formatter.format(undo=[], do=[
			Translation(fake_strokes, english) for english in translations
			], prev=None)

	return output

def translations_to_output(translations: List[Translation], initial_text: str="")->CaptureOutput:
	# Same restrictions as above.
	return translations_to_output_1(tuple(t.english for t in translations))

def translations_to_text_or_empty(translations: List[Translation], initial_text: str="")->str:
	translations=[*translations]
	for t in translations: assert isinstance(t, Translation)
	try:
		return translations_to_output(translations).text
	except:
		import traceback
		traceback.print_exc()
		return ""

class Main:
	def __init__(self, engine: "plover.engine.StenoEngine")->None:
		self._engine=engine
		#for simplicity, it will always run now

		self._simple_to_word: MutableMapping[str, str]

		try:
			self._load_wordlist()
		except:
			self._simple_to_word={}
			self._save_wordlist()

		# TODO if there are multiple words with the same simple form, only the most recently typed can be entered
		#engine.hook_connect("send_string", self.on_send_string)
		#engine.hook_connect("send_backspaces", self.on_send_backspaces)
		#engine.hook_connect("send_key_combination", self.on_send_key_combination)

		self._temporarily_disabled: bool=False

		self._running: bool=False

	def _load_wordlist(self)->None:
		data=json.load(stored_wordlist.open("r"))
		self._simple_to_word=data["simple_to_word"]

	def _save_wordlist(self)->None:
		json.dump({
			"simple_to_word": self._simple_to_word,
			},
			stored_wordlist.open("w"), indent="\t")

	def on_translated(self, old, new)->None:
		self._engine._queue.put((self.after_translated, [], {}))
		# on_translated hook is called inside formatter.format(), before output is printed
		# recursive call causes weird errors
		# generally, private method access causes weird errors
		# but there's not really any way...

	def after_translated(self)->None:
		translator=self._engine._translator
		#translator.translate_translation(...)
		#translator.translate_stroke(...)

		translations: List[Translation]=translator.get_state().translations

		# only consider 10 last translations
		part=translations[max(len(translations)-10, 0):]

		# ignore any translation that produces a combo or a command
		for i in range(len(part)-1, -1, -1):
			t=part[i]
			if (
					any(f.combo or f.command for f in t.formatting) or
					(t.english and "{#}" in t.english) # this is not counted as a combo
					):
				part=part[i+1:]
				break

		if not part: return


		# add new words...?
		output=translations_to_output(part)
		# TODO remove words in output on undoing
		# TODO editor integration


		splitter_matches=[*re.finditer(r"(\W+)", output.text)]
		if len(splitter_matches)>=2: # there's a word before the candidate
			#=> because moving left and right (right before the first operation) might corrupt the actual typed word

			#  abc) -- -- -- (def) ++ -- ghi
			#	  ~~~~~~~~~~~~	 ~~~~~~~~
			#	  ^ splitter_matches[-2]
			#					 ^ splitter_matches[-1]

			# select the penultimate => do not add partial word
			# condition: it must form a complete word (so no "a_[b_c]")

			candidate: str=output.text[splitter_matches[-2].end():splitter_matches[-1].start()]
			target1: str=output.text[:splitter_matches[-2].end()].rstrip()
			target2: str=output.text[:splitter_matches[-1].start()].rstrip()

			#print(f"* {candidate!r} {target1!r} {target2!r}")

			candidate_simple=to_simple(candidate)
			if self._simple_to_word.get(candidate_simple, None)!=candidate:
				# condition: There must be a subset of parts that form the candidate

				a: Optional[int]=None
				b: Optional[int]=None
				for i in range(1, len(part)): #not empty, not full
					cur=translations_to_text_or_empty(part[:i]).rstrip()
					#print(f"{i=} {cur=!r}")
					if cur==target1:
						a=i #choose the last one
					elif cur==target2:
						b=i #choose the first one
						break

				if a is not None and b is not None:
					part1=part[a:b]
					tmp=translations_to_text_or_empty(part1)
					#print("**", part1, tmp, candidate)
					if (tmp==candidate and
							# condition: the user has spent additional effort to form the word
							any(effective_no_op(translations_to_text_or_empty([t])) for t in part1)
							):
						L(f"Add {candidate!r}")
						self._simple_to_word[candidate_simple]=candidate

						self._save_wordlist()
						
						


			





		# find longest chunk that can be merged (not at all efficient...)

		for i in range(len(part)-1, -1, -1):
			t=part[i]
			if effective_no_op(translations_to_text_or_empty([t])):
				part=part[i+1:]
				break
		if not part: return

		for i in range(0, len(part)-1):
			part1=part[i:]
			assert len(part1)>=2
			output=translations_to_output(part1)
			assert all(
					instruction_type not in ("c", "e")
					for instruction_type, instruction_data in output.instructions)

			simple_form=to_simple(output.text)

			if (
					simple_form in self._simple_to_word # replace
					and 

					# there's no "no-op" translations

					#len(set(
					#	 to_simple(translations_to_output(part1[:i]).text)
					#	 for i in range(len(part1)+1)
					#	 ))==len(part1)+1

					all(not effective_no_op(translations_to_text_or_empty([t])) for t in part1)

					):


				#L(f"Debug: {part1=}")
				#for i in range(len(part1)+1):
				#	 L(f"{i} -> {translations_to_output(part1[:i]).text!r}")
				#L("==")


				replaced_word=self._simple_to_word[simple_form]
				assert "{" not in replaced_word
				assert "}" not in replaced_word

				L(f"Replace {output.text!r} -> {replaced_word!r}")

				new_translation=Translation(
						outline=[Stroke([])],
						translation=replaced_word+"{#}"
						)
				new_translation.replaced=part1[:-1]+part1[-1].replaced

				translator.untranslate_translation(part1[-1])
				translator.translate_translation(new_translation)
				translator.flush()

				break


		#translations_to_output(part)
		#
		## NOTE not at all efficient
		#for i in range(min(10, len(translations)), 1, -1):
		#	current = translations[-i:]
		#	# try merging parts from current...

		#	 ctx = _Context(previous_translations, last_action)
		#	 for t in do:
		#		 if t.english:
		#			 t.formatting = _translation_to_actions(t.english, ctx)
		#		 else:
		#			 t.formatting = _raw_to_actions(t.rtfcre[0], ctx)
			


	def start(self)->None:
		self._running=True
		self._engine.hook_connect("translated", self.on_translated)

	def stop(self)->None:
		self._running=False
		self._engine.hook_disconnect("translated", self.on_translated)
