"""Targeted-temperature PPO: explore stacked effects without changing greedy play.

Sampling and PPO likelihoods both use temperature 8 at declared effect choices.
Positive temperature preserves argmax, so ordinary saved-model inference is the
same greedy policy. Sampling/resume must use this runner's training wrapper.
"""
from pathlib import Path
import sys
import torch
import train_rl
from sfld_rl.shop_policy import ShopPolicyActor
from sfld_rl.effect_lab import EffectCurriculum

TEMPERATURE=8.
FRACTION=.75


class ExploringEffects(ShopPolicyActor):
    def __init__(self, base, schema):
        super().__init__(base,schema,base.shop_config)
        self.exploration_counts=torch.zeros(8,dtype=torch.int64,device=next(self.parameters()).device)

    def contexts(self, obs):
        f=lambda n:obs[:,self.oi[n]]>.5
        enough_blessings=obs[:,self.oi['blessings.1.remaining_div10']]>0
        full_curses=obs[:,self.oi['curses.2.remaining_div10']]>0
        container=f('phase.encounter')&enough_blessings&(f('encounter.crate')|f('encounter.bronze')|f('encounter.silver')|f('encounter.barrel'))
        cursed=f('phase.encounter')&full_curses&f('encounter.cursed_chest')
        blessing_door=f('phase.doors')&enough_blessings&(f('doors.0.kind.blessing')|f('doors.1.kind.blessing'))
        cursed_door=f('phase.doors')&full_curses&(f('doors.0.kind.cursed')|f('doors.1.kind.cursed'))
        return container,cursed,blessing_door,cursed_door

    def forward(self, obs):
        logits,values=super().forward(obs)
        contexts=self.contexts(obs);target=contexts[0]|contexts[1]|contexts[2]|contexts[3]
        temperature=torch.where(target,TEMPERATURE,1.)
        return logits/temperature[:,None],values

    def actions(self, observations, masks, greedy=False):
        actions,logs,values=super().actions(observations,masks,greedy)
        if not greedy:
            container,cursed,blessing_door,cursed_door=self.contexts(observations)
            chosen_blessing=((actions==self.ai['door_0'])&(observations[:,self.oi['doors.0.kind.blessing']]>.5))|((actions==self.ai['door_1'])&(observations[:,self.oi['doors.1.kind.blessing']]>.5))
            chosen_cursed=((actions==self.ai['door_0'])&(observations[:,self.oi['doors.0.kind.cursed']]>.5))|((actions==self.ai['door_1'])&(observations[:,self.oi['doors.1.kind.cursed']]>.5))
            counts=(container,container&(actions==self.ai['skip']),cursed,cursed&(actions==self.ai['interact']),
                    blessing_door,blessing_door&chosen_blessing,cursed_door,cursed_door&chosen_cursed)
            self.exploration_counts+=torch.stack([x.sum() for x in counts])
        return actions,logs,values


def main(argv=None):
    args=train_rl.parse(argv)
    if args.mode!='train' or args.resume or args.bc_steps or not args.finetune_from or args.shop_policy!='shop5' or not args.effect_curriculum or args.eval_action!='greedy':
        raise ValueError('Use a fresh shop5 fine-tune with effect curriculum, no cloning, and greedy evaluation')
    original_init=train_rl.initialize_finetune;original_metadata=train_rl.source_metadata
    curriculum_init=EffectCurriculum.__init__;model_ref=[]
    def initialize(*values,**keywords):
        model,optimizer,provenance=original_init(*values,**keywords)
        model=ExploringEffects(model,values[1].metadata['schema']);model_ref.append(model)
        return model,optimizer,provenance
    def metadata(*values,**keywords):
        result=original_metadata(*values,**keywords)
        result['sources']['tools/train_effect_exploration.py']=train_rl.sha(__file__)
        result['effect_exploration']={'temperature':TEMPERATURE,'partial_reset_fraction':FRACTION,
            'contexts':'Two or more blessings at containers/blessing doors; three curses at cursed chests/doors.',
            'greedy_inference':'Unchanged argmax at temperature 1; saved network is the deployment policy.',
            'sampling_resume':'Use this training wrapper for likelihoods. Generic sampling at temperature 1 is deployment behavior, not the exploration distribution.'}
        return result
    def curriculum(self,env,root,fraction=FRACTION):curriculum_init(self,env,root,FRACTION)
    train_rl.initialize_finetune=initialize;train_rl.source_metadata=metadata;EffectCurriculum.__init__=curriculum
    try:
        train_rl.main(argv)
        values=model_ref[0].exploration_counts.cpu().tolist()
        names=('stacked_container_visits','stacked_container_skips','full_curse_chest_visits','full_curse_chest_opens',
               'stacked_blessing_door_pairs','stacked_blessing_doors_chosen','full_curse_door_pairs','full_curse_doors_chosen')
        train_rl.write_json(Path(args.output)/'exploration-counts.json',dict(zip(names,values)))
    finally:
        train_rl.initialize_finetune=original_init;train_rl.source_metadata=original_metadata;EffectCurriculum.__init__=curriculum_init


if __name__=='__main__':main()
