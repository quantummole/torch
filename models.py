# -*- coding: utf-8 -*-
"""
Created on Mon Sep 10 12:14:19 2018

@author: quantummole
"""
import torch
import torch.nn as nn
import torch.nn.functional as tfunc
from torchvision import models
from model_blocks import DoubleConvLayer

class create_net :
    def __init__(self,net) :
        self.net = net
    def __call__(self,network_params,weights = None) :
        network = nn.DataParallel(self.net(**network_params))
        if weights :
            network.load_state_dict(torch.load(weights,map_location=lambda storage, loc: storage))
        return network

class DensenetModels(nn.Module) :
    def __init__(self,model) :
        self.model = model(pretrained=True)
        self.final_layer_features = self.model.classifier.in_features
    def update_final_layer(self,final_layer) :
        self.model.classifier = final_layer
    def forward(self,inputs) :
        if len(inputs.shape) == 3 :
            bs,m,n = inputs.shape
            inputs = inputs.view(bs,1,m,n)
        return self.model(inputs)


class ResnetModels(nn.Module) :
    def __init__(self,model) :
        self.model = model(pretrained=True)
        self.final_layer_features = self.model.fc.in_features
    def update_final_layer(self,final_layer) :
        self.model.fc = final_layer
    def forward(self,inputs) :
        if len(inputs.shape) == 3 :
            bs,m,n = inputs.shape
            inputs = inputs.view(bs,1,m,n)
        return self.model(inputs)

    
class CustomNetClassification(nn.Module):
    def __init__(self,input_dim, final_conv_dim, initial_channels,growth_factor,num_classes) :
        super(CustomNetClassification,self).__init__()
        self.layer = nn.ModuleList()
        while input_dim >= final_conv_dim :
            self.layer.append(nn.Sequential(DoubleConvLayer(initial_channels,initial_channels+growth_factor,3,1),
                                            nn.MaxPool2d(kernel_size=3,stride=2,padding=1)))
            input_dim = input_dim//2
            initial_channels += growth_factor
        num_units = input_dim*input_dim*initial_channels
        self.output_layer = nn.Sequential(nn.Linear(num_units,2*num_units),nn.ReLU(),
                                          nn.Linear(2*num_units,2*num_units),nn.ReLU(),
                                          nn.Linear(2*num_units,num_classes))
    
    def forward(self,inputs,mode=-1,debug=False) :
        outputs = []
        for inp in inputs :
            if len(inp.shape) == 3 :
                bs,m,n = inp.shape
                inp = inp.view(bs,1,m,n)
            bs,c,m,n = inp.shape
            for layer in self.layer :
                inp = layer(inp)
            inp = inp.view(bs,-1)    
            output = self.output_layer(inp)
            outputs.append(output)
        return outputs