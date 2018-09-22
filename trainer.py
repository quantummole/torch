# -*- coding: utf-8 -*-
"""
Created on Mon Sep 10 11:02:42 2018

@author: quantummole
"""


import torch
from torch.autograd import Variable
import numpy as np
from tqdm import tqdm, trange
from signals import Signal

class Trainer :
    def __init__(self,network,network_params,optimizer,optimizer_params,scheduler,scheduler_params,modes,evaluator,max_epochs,objective_fns,val_max_score=1e+5) :
        self.network = network
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.optimizer_class = optimizer
        self.scheduler_class = scheduler
        self.modes = modes
        self.Evaluator = evaluator
        self.model_file = self.Evaluator.model_file
        self.net = self.network(network_params).to(self.device)
        self.optimizer = self.optimizer_class(self.net.parameters(),**optimizer_params)
        self.scheduler = self.scheduler_class(self.optimizer,**scheduler_params)
        self.max_epochs = max_epochs
        self.objective_fns = objective_fns
        self.val_max_score = val_max_score
    def train(self,mode) :
        self.net.train()
        loss_value = 0
        grads = []
        with tqdm(self.dataloaders[mode],desc = "Training Epoch") as loader :
            for i_batch,sample_batch in enumerate(loader) :
                inputs = [Variable(inp.to(self.device), requires_grad=True) for inp in sample_batch['inputs']]
                ground_truths = [gt.to(self.device) for gt in sample_batch['ground_truths']]
                debug_info = sample_batch['debug_info']
                self.optimizer.zero_grad()
                outputs = self.net(inputs,mode)
                loss = self.objective_fns[mode](outputs,ground_truths)
                loss.backward()
                grads.append(torch.abs(inputs[0].grad).mean().item())
                self.optimizer.step()
                self.Evaluator.log(mode,[output.detach().cpu().numpy() for output in outputs],[gt.detach().cpu().numpy() for gt in ground_truths],debug_info)
                loss_value += loss.detach().item()
                loader.set_postfix(loss=(loss_value/(i_batch+1)), mode=mode, mean_grad = np.mean(grads))
        return loss_value/(i_batch+1)
            
    def validate(self) :
        with torch.no_grad():
            self.net.eval()
            score = 0
            with tqdm(self.dataloaders[0],desc="Evaluation Epoch") as loader :
                for i_batch,sample_batch in enumerate(loader) :
                    inputs = [inp.to(self.device) for inp in sample_batch['inputs']]
                    ground_truths = [gt.to(self.device) for gt in sample_batch['ground_truths']]
                    debug_info = sample_batch['debug_info']
                    outputs = self.net(inputs,mode=0)
                    score += self.objective_fns[0](outputs,ground_truths).item()
                    self.Evaluator.log(0,[output.detach().cpu().numpy() for output in outputs],[gt.detach().cpu().numpy() for gt in ground_truths],debug_info)
                    loader.set_postfix(score=(score/(i_batch+1)))
                score = score/(i_batch+1)
            return score

    def execute(self,dataloaders) :
        epoch_validations = []
        epoch_trainings = []
        self.dataloaders = dataloaders
        with trange(self.max_epochs,desc="Epochs") as epoch_iters :
            for epoch in epoch_iters :
                if issubclass(self.scheduler_class,torch.optim.lr_scheduler._LRScheduler) :
                    self.scheduler.step()
                train_loss = []
                for mode in self.modes :
                    train_loss.append(self.train(mode))
                val_loss = self.validate()
                if not issubclass(self.scheduler_class,torch.optim.lr_scheduler._LRScheduler) :
                    self.scheduler.step(val_loss)
                epoch_validations.append(val_loss)
                epoch_trainings.append(train_loss)
                if self.val_max_score >= val_loss :
                    best_val_loss = val_loss
                    torch.save(self.net.state_dict(),self.model_file)
                epoch_iters.set_postfix(best_validation_loss = best_val_loss, training_loss = train_loss,lr = self.scheduler.get_lr() )
        del self.net
        torch.cuda.empty_cache()
        return Signal.COMPLETE,"complete",[epoch_trainings,epoch_validations]
        
    
class Debugger :
    def __init__(self,network,device,ensemble_configs,weights,inference_dir,inference_fn,debug_dir,debug_fn) :
        self.ensemble = [network(**config) for config in ensemble_configs]
        self.weights = weights
        self.inference_fn = inference_fn
        self.debug_fn = debug_fn
        self.device = device
        self.debug_dir = debug_dir
        self.inference_dir = inference_dir
        
        
    def infer(self,dataloader,data_id) :
        def collate(outputs) :
            num_outputs = len(outputs[0])
            final_outputs = []
            for i in range(num_outputs) :
                output = 0
                for out in outputs :
                    output = output + out[i]
                final_outputs.append(output)
            return final_outputs

        with torch.no_grad():
            with tqdm(dataloader,desc="Batches",leave=False) as loader :
                for i_batch,sample_batch in enumerate(loader) :
                    inputs = [inp.to(self.device) for inp in sample_batch['inputs']]
                    ground_truths = [gt.to(self.device) for gt in sample_batch['ground_truths']]
                    debug_info = sample_batch['debug_info']
                    outputs = []
                    for i_net,net in enumerate(tqdm(self.ensemble,desc="Ensemble",leave=False)) :
                        net = net.to(self.device)
                        net.eval()
                        net_outputs = net(inputs,mode=-1)
                        net_outputs = [self.weights[i_net]*output for output in net_outputs]
                        outputs.append(net_outputs)
                        net = net.cpu()
                        torch.cuda.empty_cache()
                    outputs = collate(outputs)
                    self.inference_fn(self.inference_dir,data_id,debug_info,outputs,ground_truths)

    def debug(self,dataloader,data_id,model_id) :
        with torch.no_grad():
            with tqdm(dataloader,desc="Batches",leave=False) as loader :
                for i_batch,sample_batch in enumerate(loader) :
                    inputs = [inp.to(self.device) for inp in sample_batch['inputs']]
                    ground_truths = [gt.to(self.device) for gt in sample_batch['ground_truths']]
                    debug_info = sample_batch['debug_info']
                    net = self.ensemble[model_id].to(self.device)
                    net.eval()
                    net_outputs = net(inputs,mode=-2)
                    net = net.cpu()
                    torch.cuda.empty_cache()
                    self.debug_fn(self.debug_dir,data_id,debug_info,net_outputs,ground_truths)
