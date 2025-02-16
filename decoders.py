from typing import Dict, Any, Optional, List

import tensorflow as tf
from tensorflow.keras import Sequential
from tensorflow.keras import layers as tfkl

from dsp_utils.core import resample
from feature_names import FEATURE_F0_MIDI_SCALED, FEATURE_LD_SCALED

from utilities import at_least_3d

HARMONIC_OUT_ADDITIONAL = 'harmonic_out_additional'
AMP_OUT = 'amp_out'
NOISE_OUT = 'noise_out'
HARMONIC_OUT = 'harmonic_out'

class MLP(Sequential):
    """Stack Dense -> LayerNorm -> Leaky ReLU layers."""  
    def __init__(self, output_dim=256, n_layers=2, **kwargs):
        layers = []
        for _ in range(n_layers):
            layers.append(tfkl.Dense(output_dim, activation=None))
            # even though there is a layernorm in the paper,
            # with it the model in unstable
            #layers.append(tfkl.LayerNormalization())
            layers.append(tfkl.LeakyReLU())
        super().__init__(layers, **kwargs) 

class DecoderWithoutLatent(tfkl.Layer):
    """ Decoder class for loudness, F0, and possibly additional midi features. Used in the Supervised setting."""
    
    def __init__(self, rnn_channels=512, mlp_channels=512,
                 harmonic_out_channel=100, noise_out_channel=65,
                 layers_per_stack=3, timesteps=1000,
                 midi_features: Optional[List[str]] = None,
                 additional_harmonic_needed: bool = True,
                 **kwargs):
        
        super().__init__(**kwargs)

        self.MLP_f0 = MLP(mlp_channels, layers_per_stack)
        self.MLP_l = MLP(mlp_channels, layers_per_stack)

        self.MLPs_for_midi_features: Dict[str, MLP] = {}

        if midi_features is not None:
            for midi_feature in midi_features:
                self.MLPs_for_midi_features[midi_feature] = MLP(mlp_channels, layers_per_stack)

        self.rnn = tfkl.GRU(rnn_channels, return_sequences=True)
        self.MLP_rnn = MLP(mlp_channels, layers_per_stack)

        self.dense_harmonic = tfkl.Dense(harmonic_out_channel)

        self.dense_harmonic_additional: Optional[tfkl.Dense] = None
        if additional_harmonic_needed:
            self.dense_harmonic_additional: tfkl.Dense = tfkl.Dense(harmonic_out_channel)

        self.dense_amp = tfkl.Dense(1)
        self.dense_noise = tfkl.Dense(noise_out_channel)

        self.timesteps = timesteps

    def call(self, inputs: Dict[str, Any], *args, **kwargs):

        raw_inputs = inputs  # we need this as this name is later bound to something else

        x_f0 = self.MLP_f0(inputs[FEATURE_F0_MIDI_SCALED])
        x_l = self.MLP_l(inputs[FEATURE_LD_SCALED])
        inputs = [x_f0, x_l]

        for (feature_name, mlp) in self.MLPs_for_midi_features.items():
            inputs.append(mlp(raw_inputs[feature_name]))

        x = tf.concat(inputs, axis=-1)
        x = self.rnn(x)
        x = tf.concat(inputs + [x], axis=-1) 
        x = self.MLP_rnn(x)
                       
        # Synthesizer Parameters
        amp_out = self.dense_amp(x)
        harmonic_out = self.dense_harmonic(x)
        noise_out = self.dense_noise(x)

        if self.dense_harmonic_additional is not None:
            harmonic_out_additional = self.dense_harmonic_additional(x)
        else:
            harmonic_out_additional = None
        # Upsampling to the audio rate here.
        res = {AMP_OUT: self.resample(amp_out),
               HARMONIC_OUT: self.resample(harmonic_out),
               NOISE_OUT: self.resample(noise_out)}

        if harmonic_out_additional is not None:
            res[HARMONIC_OUT_ADDITIONAL] = self.resample(harmonic_out_additional)

        return res
    
    def resample(self, x):
        x = at_least_3d(x)
        return resample(x, self.timesteps, method='window')
    
class DecoderWithLatent(tfkl.Layer):
    """Decoder class for Z, F0 and l. Used in the Unsupervised Setting."""
    
    def __init__(self, rnn_channels=512, mlp_channels=512, layers_per_stack=3,
                 harmonic_out_channel=100, noise_out_channel=65,
                 timesteps=1000, **kwargs):
        
        super().__init__(**kwargs)
        
        self.MLP_f0 = MLP(mlp_channels, layers_per_stack)
        self.MLP_l = MLP(mlp_channels, layers_per_stack)
        self.MLP_z = MLP(mlp_channels, layers_per_stack)

        self.rnn = tfkl.GRU(rnn_channels, return_sequences=True)
        self.MLP_rnn = MLP(mlp_channels, layers_per_stack)
        
        self.dense_harmonic = tfkl.Dense(harmonic_out_channel)
        self.dense_amp = tfkl.Dense(1)
        self.dense_noise = tfkl.Dense(noise_out_channel)
        
        self.timesteps = timesteps

    def call(self, inputs):
        
        x_f0 = self.MLP_f0(inputs['f0_midi_scaled'])
        x_l = self.MLP_l(inputs['ld_scaled'])
        x_z = self.MLP_z(inputs['z'])
        
        inputs = [x_f0, x_l, x_z]
        x = tf.concat(inputs, axis=-1)
          
        x = self.rnn(x)
        
        x = tf.concat(inputs + [x], axis=-1)
        x = self.MLP_rnn(x)
                       
        # Parameters of the synthesizers
        amp_out = self.dense_amp(x)
        harmonic_out = self.dense_harmonic(x)
        noise_out = self.dense_noise(x)
    
        return {'amp_out': self.resample(amp_out),
                'harmonic_out': self.resample(harmonic_out),
                'noise_out': self.resample(noise_out)}
    
    def resample(self, x):
        x = at_least_3d(x)
        return resample(x, self.timesteps, method='window')