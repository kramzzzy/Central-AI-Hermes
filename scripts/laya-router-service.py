"""Run Laya's server with language taking priority over implicit workflow routing."""
import os
from pathlib import Path


def service_key(environment):
    """Require the managed service's mounted authentication key."""
    path = environment.get('LAYA_API_KEY_FILE')
    if not path:
        raise RuntimeError('Laya requires its private API key file')
    key = Path(path).read_text(encoding='utf-8').strip()
    if len(key) < 32 or any(char in key for char in '\r\n\0'):
        raise RuntimeError('Invalid Laya service key')
    return key


if __name__ == '__main__':
    os.environ['LAYA_API_KEY'] = service_key(os.environ)

import laya.router as routing
from laya.serve import main


class SituationRouter(routing.Router):
    def _route(self, state, questions=None, model=None, task=None, lang=None, lang_guess=None):
        decision = super()._route(state, questions, model=model, task=task, lang=lang, lang_guess=lang_guess)
        if model is None and task is None and decision['model'] == 'typed-decisions':
            language = super()._route(state, None, lang=lang, lang_guess=lang_guess)
            if language['model'] == 'multilingual':
                language['workflow'] = decision['workflow']
                language['reason'] += '; specialist is English-only'
                return language
        return decision


if __name__ == '__main__':
    # Standalone repositories have separately reviewed checkpoint revisions.
    # A fresh server downloads them into its persistent cache on first start.
    routing.DEFAULT_MODELS.update({name: (repo, None)
                                   for name, repo in routing.STANDALONE_MODELS.items()})
    routing.Router = SituationRouter
    main()
